"""
Read-Only Payment Connector for AI (Mathia)
Strict permissions: Can only READ payment data, cannot initiate transactions
"""
import logging
from decimal import Decimal
from orchestration.base_connector import BaseConnector

logger = logging.getLogger(__name__)


class ReadOnlyPaymentConnector(BaseConnector):
    """
    AI-safe payment connector with read-only permissions
    """
    
    async def execute(self, parameters: dict, context: dict) -> dict:
        """
        Execute read-only payment queries
        
        Allowed actions:
        - check_balance
        - list_transactions
        - check_invoice_status
        
        Forbidden actions (will return error):
        - create_invoice
        - withdraw
        - transfer
        """
        action = parameters.get("action")
        
        # Whitelist of allowed actions
        ALLOWED_ACTIONS = ['check_balance', 'list_transactions', 'check_invoice_status', 'check_payments']
        
        if action not in ALLOWED_ACTIONS:
            return {
                "error": f"AI does not have permission for action: {action}",
                "message": "Payment operations are restricted to read-only access for AI"
            }
        
        from django.contrib.auth import get_user_model
        from asgiref.sync import sync_to_async
        User = get_user_model()
        user_id = context.get("user_id")
        
        try:
            user = await sync_to_async(User.objects.get)(id=user_id)
        except Exception:
            return {"status": "error", "message": "User not found"}

        # Route to appropriate handler
        if action == "check_balance":
            return await self.check_balance(user)
        elif action == "list_transactions":
            return await self.list_transactions(user, parameters.get("limit", 10))
        elif action == "check_invoice_status":
            return await self.check_invoice_status(parameters.get("invoice_id"))
        elif action == "check_payments":
             # Summary view: Balance + Last 3 transactions
            balance_data = await self.check_balance(user)
            tx_data = await self.list_transactions(user, limit=3)
            
            return {
                "status": "success",
                "balance": balance_data.get("balance", 0),
                "currency": balance_data.get("currency", "KES"),
                "recent_transactions": tx_data.get("transactions", []),
                "message": f"Your balance is {balance_data.get('balance', 0)} {balance_data.get('currency', 'KES')}. Here are your last 3 transactions."
            }
        
        return {"error": "Unknown action"}
    
    async def check_balance(self, user) -> dict:
        """Get user's current wallet balance"""
        from payments.services import WalletService
        from asgiref.sync import sync_to_async
        
        try:
            balance = await sync_to_async(WalletService.get_balance)(user)
            
            return {
                "status": "success",
                "balance": float(balance),
                "currency": "KES",
                "message": f"Your current balance is {balance} KES"
            }
        except Exception as e:
            logger.error(f"Error checking balance: {e}")
            return {"error": str(e)}
    
    async def list_transactions(self, user, limit: int = 10) -> dict:
        """List recent transactions"""
        from payments.models import LedgerAccount
        from asgiref.sync import sync_to_async
        
        try:
            def _get_transactions():
                try:
                    wallet = LedgerAccount.objects.get(user=user, account_type='LIABILITY')
                    recent_entries = wallet.entries.select_related(
                        'journal_entry'
                    ).order_by('-journal_entry__timestamp')[:limit]
                    
                    transactions = []
                    for entry in recent_entries:
                        transactions.append({
                            'date': entry.journal_entry.timestamp.strftime('%Y-%m-%d %H:%M'),
                            'description': entry.journal_entry.description,
                            'amount': float(entry.amount if entry.dr_cr == 'CREDIT' else -entry.amount),
                            'type': entry.journal_entry.get_transaction_type_display(),
                        })
                    
                    return transactions
                except LedgerAccount.DoesNotExist:
                    return []
            
            transactions = await sync_to_async(_get_transactions)()
            
            return {
                "status": "success",
                "transactions": transactions,
                "count": len(transactions)
            }
        except Exception as e:
            logger.error(f"Error listing transactions: {e}")
            return {"error": str(e)}
    
    async def check_invoice_status(self, invoice_id: str) -> dict:
        """Check status of an invoice"""
        from payments.models import PaymentRequest
        from asgiref.sync import sync_to_async
        
        try:
            def _get_invoice_status():
                try:
                    invoice = PaymentRequest.objects.get(reference_id=invoice_id)
                    return {
                        "reference_id": str(invoice.reference_id),
                        "amount": float(invoice.amount),
                        "status": invoice.get_status_display(),
                        "created": invoice.created_at.strftime('%Y-%m-%d %H:%M'),
                        "paid": invoice.paid_at.strftime('%Y-%m-%d %H:%M') if invoice.paid_at else None,
                    }
                except PaymentRequest.DoesNotExist:
                    return None
            
            invoice_data = await sync_to_async(_get_invoice_status)()
            
            if invoice_data:
                return {
                    "status": "success",
                    "invoice": invoice_data
                }
            else:
                return {"error": "Invoice not found"}
                
        except Exception as e:
            logger.error(f"Error checking invoice: {e}")
            return {"error": str(e)}
