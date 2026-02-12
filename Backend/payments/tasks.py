"""
Celery tasks for payment processing
"""
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


@shared_task
def reconcile_ledger():
    """
    Nightly reconciliation job
    Runs at 2:00 AM daily via Celery Beat
    """
    from .services import LedgerService
    
    logger.info("Starting nightly reconciliation")
    
    try:
        LedgerService.reconcile_daily()
        logger.info("Reconciliation completed successfully")
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}")
        raise


@shared_task
def process_recurring_invoices():
    """
    Process recurring invoices daily
    """
    from .models import PaymentRequest
    from .services import InvoiceService
    
    logger.info("Processing recurring invoices")
    
    today = timezone.now().date()
    due_invoices = PaymentRequest.objects.filter(
        is_recurring=True,
        next_billing_date=today,
        status='PAID'
    )
    
    for invoice in due_invoices:
        try:
            # Create new invoice based on recurring schedule
            new_invoice = InvoiceService.create_invoice(
                issuer=invoice.issuer,
                amount=invoice.amount,
                description=f"Recurring: {invoice.description}",
                payer_email=invoice.payer_email,
                recurrence=invoice.recurrence_interval
            )
            
            new_invoice.parent_invoice = invoice
            new_invoice.save()
            
            # Update next billing date on original
            if invoice.recurrence_interval == 'MONTHLY':
                invoice.next_billing_date = today + timedelta(days=30)
            elif invoice.recurrence_interval == 'QUARTERLY':
                invoice.next_billing_date = today + timedelta(days=90)
            elif invoice.recurrence_interval == 'YEARLY':
                invoice.next_billing_date = today + timedelta(days=365)
            
            invoice.save()
            logger.info(f"Created recurring invoice: {new_invoice.reference_id}")
            
        except Exception as e:
            logger.error(f"Failed to process recurring invoice {invoice.id}: {e}")
