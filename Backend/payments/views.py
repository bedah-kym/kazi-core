"""
Payment views for wallet and invoice management
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.contrib import messages
from decimal import Decimal
import json
import logging

from .models import PaymentRequest, JournalEntry, PaymentNotification, LedgerAccount
from .services import WalletService, InvoiceService, LedgerService
from users.decorators import workspace_required

logger = logging.getLogger(__name__)


@workspace_required
def wallet_dashboard(request):
    """
    Main wallet dashboard showing balance and recent transactions
    """
    user = request.user
    balance = WalletService.get_balance(user)
    
    # Get user's wallet account
    try:
        wallet = LedgerAccount.objects.get(user=user, account_type='LIABILITY')
        
        # Get recent transactions
        recent_entries = wallet.entries.select_related(
            'journal_entry'
        ).order_by('-journal_entry__timestamp')[:20]
        
        transactions = []
        for entry in recent_entries:
            transactions.append({
                'date': entry.journal_entry.timestamp,
                'description': entry.journal_entry.description,
                'amount': entry.amount if entry.dr_cr == 'CREDIT' else -entry.amount,
                'type': entry.journal_entry.get_transaction_type_display(),
                'reference': entry.journal_entry.reference_id
            })
    except LedgerAccount.DoesNotExist:
        transactions = []
    
    # Get unread notifications
    notifications = PaymentNotification.objects.filter(
        user=user,
        is_read=False
    ).order_by('-created_at')[:5]
    
    context = {
        'balance': balance,
        'transactions': transactions,
        'notifications': notifications,
        'workspace': request.user.workspace,
    }
    
    return render(request, 'payments/wallet_dashboard.html', context)


@login_required
def initiate_deposit(request):
    """
    Initiate a deposit via IntaSend
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        amount = Decimal(request.POST.get('amount', 0))
        
        if amount < Decimal('1.00'):
            return JsonResponse({'error': 'Minimum deposit is 1 KES'}, status=400)
        
        # Generate payment link via IntaSend
        from intasend import APIService
        import os
        
        publishable_key = os.environ.get('INTASEND_PUBLISHABLE_KEY')
        api_key = os.environ.get('INTASEND_API_KEY')
        is_test = os.environ.get('INTASEND_IS_TEST', 'True').lower() == 'true'
        
        if not publishable_key or not api_key:
            return JsonResponse({'error': 'Payment gateway not configured'}, status=500)
        
        # Initialize APIService
        service = APIService(token=api_key, publishable_key=publishable_key, test=is_test)
        
        # STK Push for M-Pesa using Collect service
        from intasend import Collect
        collect = Collect(token=api_key, publishable_key=publishable_key, test=is_test)
        
        phone = request.POST.get('phone', '')
        
        if phone:
            response = collect.mpesa_stk_push(
                phone_number=phone,
                email=request.user.email,
                amount=float(amount),
                narrative=f"Wallet deposit - {request.user.username}"
            )
            
            # The 'invoice' key is standard in IntaSend response
            invoice_id = response.get('invoice', {}).get('invoice_id')
            # Or handle different response structure if needed
            if not invoice_id and 'id' in response: # Some versions return direct ID
                 invoice_id = response['id']
            
            return JsonResponse({
                'status': 'success',
                'message': 'Payment request sent to your phone',
                'tracking_id': invoice_id
            })
        else:
            return JsonResponse({'error': 'Phone number required'}, status=400)
            
    except Exception as e:
        logger.error(f"Deposit initiation error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def payment_callback(request):
    """
    IntaSend webhook callback for payment confirmation
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        # Parse webhook data
        data = json.loads(request.body)
        
        # Verify webhook signature (implement based on IntaSend docs)
        # signature = request.headers.get('X-IntaSend-Signature')
        # if not verify_signature(data, signature):
        #     return JsonResponse({'error': 'Invalid signature'}, status=403)
        
        invoice_id = data.get('invoice_id')
        state = data.get('state')
        gross_amount = Decimal(str(data.get('value', 0)))
        fee = Decimal(str(data.get('fee', 0)))
        
        # Find user by email or other identifier
        email = data.get('email')
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.error(f"User not found for email: {email}")
            return JsonResponse({'error': 'User not found'}, status=404)
        
        if state == 'COMPLETE':
            # Process deposit
            journal = WalletService.process_deposit(
                user=user,
                gross_amount=gross_amount,
                intasend_fee=fee,
                provider_ref=invoice_id
            )
            
            logger.info(f"Deposit processed: {journal.reference_id}")
            return JsonResponse({'status': 'success'})
        
        return JsonResponse({'status': 'ignored', 'state': state})
        
    except Exception as e:
        logger.error(f"Callback processing error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@workspace_required
def create_invoice_view(request):
    """
    Create a new payment request/invoice
    """
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount'))
            description = request.POST.get('description', '')
            recipient_email = request.POST.get('recipient_email', '')
            recurrence = request.POST.get('recurrence', 'NONE')
            
            invoice = InvoiceService.create_invoice(
                issuer=request.user,
                amount=amount,
                description=description,
                payer_email=recipient_email,
                recurrence=recurrence
            )
            
            messages.success(request, f'Invoice created! Share link: {invoice.intasend_payment_link}')
            return redirect('payments:invoice_detail', reference_id=invoice.reference_id)
            
        except Exception as e:
            messages.error(request, f'Error creating invoice: {str(e)}')
            return redirect('payments:wallet_dashboard')
    
    context = {
        'workspace': request.user.workspace,
    }
    return render(request, 'payments/create_invoice.html', context)


@login_required
def invoice_detail(request, reference_id):
    """
    View invoice details
    """
    invoice = get_object_or_404(PaymentRequest, reference_id=reference_id)
    
    # Check permission (issuer or payer can view)
    if invoice.issuer != request.user and invoice.payer != request.user:
        messages.error(request, 'Access denied')
        return redirect('payments:wallet_dashboard')
    
    context = {
        'invoice': invoice,
        'workspace': request.user.workspace,
    }
    return render(request, 'payments/invoice_detail.html', context)


# API Endpoints for read-only access (for AI/Mathia)

@login_required
def get_balance_api(request):
    """
    Get user's wallet balance (READ-ONLY)
    """
    balance = WalletService.get_balance(request.user)
    return JsonResponse({
        'balance': float(balance),
        'currency': 'KES'
    })


@login_required
def list_transactions_api(request):
    """
    List recent transactions (READ-ONLY)
    """
    try:
        wallet = LedgerAccount.objects.get(user=request.user, account_type='LIABILITY')
        recent_entries = wallet.entries.select_related(
            'journal_entry'
        ).order_by('-journal_entry__timestamp')[:10]
        
        transactions = []
        for entry in recent_entries:
            transactions.append({
                'date': entry.journal_entry.timestamp.isoformat(),
                'description': entry.journal_entry.description,
                'amount': float(entry.amount if entry.dr_cr == 'CREDIT' else -entry.amount),
                'type': entry.journal_entry.transaction_type,
            })
        
        return JsonResponse({'transactions': transactions})
    except LedgerAccount.DoesNotExist:
        return JsonResponse({'transactions': []})
