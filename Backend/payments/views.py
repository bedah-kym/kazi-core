"""
Payment views for wallet and invoice management
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Sum
from django.contrib import messages
from django.conf import settings
from decimal import Decimal
import uuid
import json
import logging

from .models import PaymentRequest, PaymentNotification, FeeSchedule
from .services import WalletService, InvoiceService
from users.models import WalletTransaction
from users.decorators import workspace_required

logger = logging.getLogger(__name__)


@workspace_required
def wallet_dashboard(request):
    """
    Main wallet dashboard showing balance and recent transactions
    """
    user = request.user
    balance = WalletService.get_balance(user)

    wallet = WalletService.get_or_create_user_wallet(user)
    recent_entries = WalletTransaction.objects.filter(
        wallet=wallet
    ).order_by('-created_at')[:20]

    transactions = []
    for entry in recent_entries:
        amount = entry.amount if entry.type == 'CREDIT' else -entry.amount
        transactions.append({
            'date': entry.created_at,
            'description': entry.description,
            'amount': amount,
            'type': entry.get_type_display(),
            'reference': entry.reference
        })

    # Get unread notifications
    notifications = PaymentNotification.objects.filter(
        user=user,
        is_read=False
    ).order_by('-created_at')[:5]

    now = timezone.now()
    invoice_qs = PaymentRequest.objects.filter(issuer=user)
    active_invoices = invoice_qs.filter(status='PENDING', expires_at__gt=now).count()
    pending_invoices = invoice_qs.filter(status='PENDING').count()
    overdue_invoices = invoice_qs.filter(status='PENDING', expires_at__lte=now).count()

    total_revenue = WalletTransaction.objects.filter(
        wallet=wallet,
        type='CREDIT',
        status='COMPLETED'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    credit_count = WalletTransaction.objects.filter(
        wallet=wallet,
        type='CREDIT',
        status='COMPLETED'
    ).count()

    last_tx_at = recent_entries[0].created_at if recent_entries else None

    try:
        fee_config = FeeSchedule.objects.get(transaction_type='DEPOSIT', is_active=True)
        platform_fee = fee_config.platform_fee
    except FeeSchedule.DoesNotExist:
        platform_fee = None

    context = {
        'balance': balance,
        'transactions': transactions,
        'notifications': notifications,
        'workspace': request.user.workspace,
        'currency': wallet.currency,
        'active_invoices': active_invoices,
        'pending_invoices': pending_invoices,
        'overdue_invoices': overdue_invoices,
        'total_revenue': total_revenue,
        'credit_count': credit_count,
        'last_tx_at': last_tx_at,
        'platform_fee': platform_fee,
    }

    return render(request, 'payments/wallet_dashboard.html', context)


@workspace_required
def transactions_view(request):
    """
    Full transaction history for the workspace wallet
    """
    wallet = WalletService.get_or_create_user_wallet(request.user)
    transactions = WalletTransaction.objects.filter(wallet=wallet).order_by('-created_at')[:200]

    context = {
        'transactions': transactions,
        'workspace': request.user.workspace,
    }

    return render(request, 'payments/transactions.html', context)


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
        
        # Create a hosted payment link via IntaSend
        import os
        
        publishable_key = os.environ.get('INTASEND_PUBLISHABLE_KEY')
        api_key = os.environ.get('INTASEND_API_KEY')
        is_test = os.environ.get('INTASEND_IS_TEST', 'True').lower() == 'true'
        
        if not publishable_key or not api_key:
            return JsonResponse({'error': 'Payment gateway not configured'}, status=500)
        
        from intasend import APIService
        from django.urls import reverse
        service = APIService(token=api_key, publishable_key=publishable_key, test=is_test)

        response = service.collect.checkout(
            amount=float(amount),
            currency="KES",
            email=request.user.email,
            api_ref=f"wallet:{request.user.id}",
            comment=f"Wallet deposit - {request.user.username}",
            mobile_tarrif="BUSINESS-PAYS",
            redirect_url=request.build_absolute_uri(reverse('payments:wallet_dashboard')),
        )

        payment_link = response.get('url') or response.get('payment_link')
        invoice_id = response.get('invoice_id') or response.get('id')

        if not payment_link:
            return JsonResponse({'error': 'Payment link not returned by gateway'}, status=502)

        return JsonResponse({
            'status': 'success',
            'message': 'Redirecting to payment page',
            'payment_link': payment_link,
            'tracking_id': invoice_id,
        })
            
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
        # Verify webhook signature
        from orchestration.webhook_validator import verify_intasend_signature, log_webhook_verification
        signature = request.headers.get('X-IntaSend-Signature') or request.headers.get('X-IntaSend-Secret') or request.headers.get('X-IntaSend-Challenge')
        secret = getattr(settings, 'INTASEND_WEBHOOK_SECRET', None)
        if not secret:
            logger.error("INTASEND_WEBHOOK_SECRET not configured")
            return JsonResponse({'error': 'Webhook not configured'}, status=500)

        raw_body = request.body if isinstance(request.body, bytes) else request.body.encode()
        if not verify_intasend_signature(signature, secret, raw_body):
            logger.warning(f"Invalid IntaSend webhook signature from {request.META.get('REMOTE_ADDR')}")
            log_webhook_verification('intasend', False)
            return JsonResponse({'error': 'Invalid signature'}, status=401)

        log_webhook_verification('intasend', True)

        # Parse webhook data
        data = json.loads(raw_body)
        
        invoice_id = (
            data.get('invoice_id')
            or data.get('id')
            or data.get('tracking_id')
            or data.get('invoice')
        )
        state = data.get('state')
        gross_amount = Decimal(str(data.get('value') or data.get('amount') or 0))
        fee = Decimal(str(data.get('fee') or 0))
        api_ref = data.get('api_ref') or data.get('api_ref_id')

        invoice = None
        if invoice_id:
            invoice = PaymentRequest.objects.filter(intasend_invoice_id=invoice_id).first()
            if not invoice:
                try:
                    invoice_uuid = uuid.UUID(str(invoice_id))
                    invoice = PaymentRequest.objects.filter(reference_id=invoice_uuid).first()
                except (ValueError, TypeError):
                    invoice = None

        if invoice:
            if not invoice.intasend_invoice_id:
                invoice.intasend_invoice_id = invoice_id
            if data.get('email') and not invoice.payer_email:
                invoice.payer_email = data.get('email')
            invoice.save(update_fields=['intasend_invoice_id', 'payer_email'])

            from workflows.webhook_handlers import handle_intasend_webhook_event
            handle_intasend_webhook_event(invoice.issuer_id, data)

            if state in ('COMPLETE', 'COMPLETED'):
                try:
                    InvoiceService.process_invoice_payment(invoice.id, invoice_id)
                except Exception as e:
                    logger.error(f"Invoice payment processing error: {e}")
                    return JsonResponse({'error': 'Invoice processing failed'}, status=500)
                return JsonResponse({'status': 'success'})

            if state in ('FAILED', 'CANCELLED', 'EXPIRED') and invoice.status == 'PENDING':
                invoice.status = 'CANCELLED' if state != 'EXPIRED' else 'EXPIRED'
                invoice.save(update_fields=['status'])
            return JsonResponse({'status': 'ignored', 'state': state})
        
        # Find user by api_ref or email for wallet deposits
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = None
        if api_ref:
            try:
                if str(api_ref).startswith("wallet:"):
                    parts = str(api_ref).split(":")
                    if len(parts) >= 2 and parts[1].isdigit():
                        user = User.objects.filter(id=int(parts[1])).first()
                if not user and str(api_ref).isdigit():
                    user = User.objects.filter(id=int(api_ref)).first()
            except Exception:
                user = None

        if not user:
            email = data.get('email')
            if email:
                user = User.objects.filter(email=email).first()

        if not user:
            logger.error(f"User not found for deposit: api_ref={api_ref}, email={data.get('email')}")
            return JsonResponse({'error': 'User not found'}, status=404)

        from workflows.webhook_handlers import handle_intasend_webhook_event
        handle_intasend_webhook_event(user.id, data)

        if state in ('COMPLETE', 'COMPLETED'):
            # Process deposit
            tx = WalletService.process_deposit(
                user=user,
                gross_amount=gross_amount,
                intasend_fee=fee,
                provider_ref=invoice_id
            )

            logger.info(f"Deposit processed: {tx.reference}")
            return JsonResponse({'status': 'success'})

        return JsonResponse({'status': 'ignored', 'state': state})
        
    except Exception as e:
        logger.error(f"Callback processing error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def deposit_status(request):
    """
    Poll deposit status by IntaSend tracking id.
    """
    tracking_id = request.GET.get('tracking_id')
    if not tracking_id:
        return JsonResponse({'status': 'error', 'message': 'tracking_id required'}, status=400)

    wallet = WalletService.get_or_create_user_wallet(request.user)
    tx = WalletTransaction.objects.filter(wallet=wallet, reference=tracking_id).first()
    if not tx:
        return JsonResponse({'status': 'pending'})

    return JsonResponse({
        'status': tx.status.lower(),
        'amount': float(tx.amount),
        'currency': tx.currency,
        'reference': tx.reference,
    })


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
            
            from django.urls import reverse
            
            invoice = InvoiceService.create_invoice(
                issuer=request.user,
                amount=amount,
                description=description,
                payer_email=recipient_email,
                recurrence=recurrence,
                redirect_url=request.build_absolute_uri(reverse('payments:wallet_dashboard'))
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
        'workspace': getattr(request.user, 'workspace', None),
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
    wallet = WalletService.get_or_create_user_wallet(request.user)
    recent_entries = WalletTransaction.objects.filter(
        wallet=wallet
    ).order_by('-created_at')[:10]

    transactions = []
    for entry in recent_entries:
        amount = entry.amount if entry.type == 'CREDIT' else -entry.amount
        transactions.append({
            'date': entry.created_at.isoformat(),
            'description': entry.description,
            'amount': float(amount),
            'type': entry.type,
        })

    return JsonResponse({'transactions': transactions})
