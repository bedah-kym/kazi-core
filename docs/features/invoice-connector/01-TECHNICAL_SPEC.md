# Invoice Connector - Technical Specification

**Status:** ✅ Implemented (v1.0)  
**Owner:** GPT-5 (Implemented Feb 3, 2026)  
**Last Updated:** February 3, 2026  
**Related Files:**
- `Backend/orchestration/connectors/invoice_connector.py`
- `Backend/orchestration/mcp_router.py` (registration: `"create_invoice": InvoiceConnector()`)

---

## 1. Overview

### Purpose
The **Invoice Connector** enables users to create payment invoices programmatically. When integrated with workflows, it allows:

- Automatic invoice creation when payment received
- Batch invoice generation from user requests
- Integration with email delivery (send invoice link to client)
- Support for payment provider integrations (IntaSend, Stripe, etc.)

### Typical Use Case

**Scenario:** Freelancer receives payment via IntaSend and wants to automatically create an invoice to send to client.

```
Workflow:
1. User receives payment (IntaSend webhook)
2. Create invoice with payment amount
3. Email invoice link to client
4. Send confirmation WhatsApp to freelancer
```

### Key Capabilities
- ✅ **Invoice Generation** - Create invoices with amount, description, client details
- ✅ **Email Delivery** - Send invoice link/PDF to client email
- ✅ **Payment Provider Integration** - Connect with IntaSend, Stripe, etc.
- ✅ **Unique Invoice IDs** - Auto-generated invoice numbers
- ✅ **Metadata Storage** - Track invoice source (workflow, manual, etc.)
- ✅ **Status Tracking** - Draft, sent, paid, overdue states

---

## 2. Architecture

### Connector Structure

```
InvoiceConnector (extends BaseConnector)
├── execute(parameters, context)
│   ├── Validate input
│   ├── Generate invoice ID
│   ├── Store invoice record
│   ├── (Optional) Generate PDF
│   ├── (Optional) Send email
│   └── Return result
├── _generate_invoice_id()
├── _validate_amount()
└── _send_invoice_email(invoice_id, recipient_email)
```

### Execution Flow

```
User/Workflow calls: "create_invoice"
    ↓
MCP Router (mcp_router.py)
    ├─ Look up: InvoiceConnector
    ├─ Extract parameters (amount, client_email, etc.)
    └─ Call: connector.execute(parameters, context)
        ↓
    InvoiceConnector
    ├─ Validate amount (min 100, max 10,000,000)
    ├─ Generate Invoice ID (INV-2026-xxxxx)
    ├─ Create Invoice record in database
    ├─ (If email requested) Generate payment link
    │   └─ Call IntaSend / Stripe API
    ├─ (If email requested) Send email via Mailgun
    │   └─ POST /api/mailgun/send/
    ├─ Return result
    │   {
    │     "status": "success",
    │     "invoice_id": "INV-2026-12345",
    │     "amount": 50000,
    │     "email_sent": true,
    │     "payment_link": "https://intasend.com/pay/..."
    │   }
    └─ Caller processes result
```

---

## 3. Data Models

### Invoice Model

```python
class Invoice(models.Model):
    """Represents a single invoice"""
    
    # Identification
    invoice_id = CharField(unique=True)  # e.g., "INV-2026-12345"
    user = ForeignKey(User)  # Invoice owner
    
    # Basic info
    amount = DecimalField(max_digits=10, decimal_places=2)
    currency = CharField(default='KES')  # KES, USD, GBP, etc.
    description = TextField()
    
    # Client info
    client_name = CharField()
    client_email = EmailField()
    client_phone = CharField(null=True)
    
    # Status tracking
    status = CharField(
        choices=[
            ('draft', 'Draft'),
            ('sent', 'Sent to client'),
            ('paid', 'Payment received'),
            ('overdue', 'Overdue'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft'
    )
    
    # Payment tracking
    payment_link = URLField(null=True)  # IntaSend / Stripe link
    payment_provider = CharField(null=True)  # intasend, stripe, paypal
    payment_received_at = DateTimeField(null=True)
    
    # Audit
    created_by_workflow = ForeignKey(UserWorkflow, null=True, blank=True)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
    
    # Email tracking
    email_sent_at = DateTimeField(null=True)
    email_opened_count = IntegerField(default=0)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['status']),
        ]
```

---

## 4. REST API Endpoint

### Create Invoice

#### **POST** `/api/connectors/create_invoice/`

**Authentication:** Bearer Token (required)  
**Content-Type:** `application/json`

**Request:**
```json
{
  "parameters": {
    "amount": 50000,
    "currency": "KES",
    "description": "Website redesign - 5 pages",
    "client_name": "Acme Corp",
    "client_email": "accounts@acme.com",
    "client_phone": "+254712345678",
    "send_email": true,
    "payment_provider": "intasend"
  },
  "context": {
    "user_id": 42,
    "room_id": "room_123"
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `amount` | integer | Yes | Invoice amount in smallest currency unit (e.g., 50000 = KES 500) |
| `currency` | string | No | Currency code (default: KES) |
| `description` | string | Yes | What is being invoiced for |
| `client_name` | string | Yes | Client's name |
| `client_email` | string | Yes | Email address to send invoice to |
| `client_phone` | string | No | Client's phone number |
| `send_email` | boolean | No | Whether to send invoice email (default: true) |
| `payment_provider` | string | No | intasend, stripe, paypal (default: intasend) |

**Response (201 Created):**
```json
{
  "status": "success",
  "invoice_id": "INV-2026-12345",
  "amount": 50000,
  "currency": "KES",
  "description": "Website redesign - 5 pages",
  "client_name": "Acme Corp",
  "client_email": "accounts@acme.com",
  "status": "sent",
  "payment_link": "https://intasend.com/pay/PAY-abc123/",
  "email_sent": true,
  "created_at": "2026-02-03T14:30:00Z"
}
```

**Error Response (400 Bad Request):**
```json
{
  "status": "error",
  "message": "Amount must be between KES 100 and KES 10,000,000",
  "field": "amount"
}
```

**Error Codes:**

| Code | Meaning | Solution |
|------|---------|----------|
| 400 | Validation failed | Check amount, email, description |
| 401 | Unauthorized | Provide valid Bearer token |
| 422 | Unprocessable | Missing required fields |
| 500 | Server error | Check logs, IntaSend API status |
| 503 | Email service down | Check Mailgun status |

---

## 5. Integration with Workflows

### Example: Payment → Invoice Workflow

**Workflow Definition:**
```json
{
  "name": "Invoice on Payment",
  "description": "Create invoice when customer pays via IntaSend",
  "triggers": [
    {
      "type": "webhook",
      "service": "intasend",
      "event": "payment.confirmed"
    }
  ],
  "steps": [
    {
      "id": "create_invoice",
      "service": "payment",
      "action": "create_invoice",
      "params": {
        "amount": "{{ trigger.amount }}",
        "description": "Payment confirmation invoice",
        "client_name": "{{ trigger.customer_name }}",
        "client_email": "{{ trigger.customer_email }}",
        "send_email": true,
        "payment_provider": "intasend"
      }
    },
    {
      "id": "notify_freelancer",
      "service": "whatsapp",
      "action": "send_whatsapp",
      "params": {
        "phone": "{{ trigger.vendor_phone }}",
        "message": "Invoice #{{ create_invoice.invoice_id }} sent to client"
      }
    }
  ]
}
```

**Webhook Payload:**
```json
POST /api/workflows/webhooks/1/

{
  "event": "payment.confirmed",
  "data": {
    "payment_id": "PAY-123",
    "amount": 50000,
    "customer_name": "John Doe",
    "customer_email": "john@example.com",
    "vendor_phone": "+254712345678"
  }
}
```

**Temporal Execution:**
1. Receive webhook
2. Parse trigger data
3. Call `create_invoice` activity with merged params
4. Invoice created, email sent
5. Call `send_whatsapp` activity
6. Notify freelancer on WhatsApp
7. Execution completes with all results

---

## 6. Email Integration

### Email Template

When `send_email: true`, Mailgun sends:

**Subject:** `Invoice #INV-2026-12345`

**Body (HTML):**
```html
<h2>Invoice #INV-2026-12345</h2>

<p>Dear Acme Corp,</p>

<p>Thank you for your business. Please see the invoice details below:</p>

<table style="border-collapse: collapse; width: 100%;">
  <tr>
    <td style="border: 1px solid #ccc; padding: 10px;">Description</td>
    <td style="border: 1px solid #ccc; padding: 10px;">Website redesign - 5 pages</td>
  </tr>
  <tr>
    <td style="border: 1px solid #ccc; padding: 10px;">Amount</td>
    <td style="border: 1px solid #ccc; padding: 10px;">KES 500.00</td>
  </tr>
  <tr>
    <td style="border: 1px solid #ccc; padding: 10px;">Due Date</td>
    <td style="border: 1px solid #ccc; padding: 10px;">2026-02-10</td>
  </tr>
</table>

<p style="margin-top: 20px;">
  <a href="https://intasend.com/pay/PAY-abc123/" style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none;">Pay Now</a>
</p>

<p>Questions? Reply to this email.</p>
<p>- Mathia</p>
```

### Email Delivery Status

```python
# Check if email was sent
invoice = Invoice.objects.get(invoice_id='INV-2026-12345')
if invoice.email_sent_at:
    print(f"Email sent at {invoice.email_sent_at}")
else:
    print("Email not yet sent")

# Track opens (Mailgun webhook)
invoice.email_opened_count  # Number of times client opened email
```

---

## 7. Payment Provider Integration

### IntaSend Integration (Default)

**Configuration:**
```python
# settings.py
INTASEND_PUBLIC_KEY = os.environ.get('INTASEND_PUBLIC_KEY')
INTASEND_API_KEY = os.environ.get('INTASEND_API_KEY')
```

**Flow:**
1. Call IntaSend API: `POST /api/v1/payment-links/`
2. Get payment link: `https://intasend.com/pay/PAY-abc123/`
3. Send link to client via email
4. Client clicks link, pays
5. IntaSend sends webhook: `POST /api/workflows/webhooks/invoice/1/`
6. Mark invoice as "paid"

### Stripe Integration (Future)

```json
{
  "parameters": {
    "amount": 50000,
    "payment_provider": "stripe"
  }
}
```

**Would create Stripe PaymentIntent and return link.**

---

## 8. Configuration & Deployment

### Environment Variables

```bash
# IntaSend (required for payment links)
INTASEND_PUBLIC_KEY=pk_test_xxxx
INTASEND_API_KEY=sk_test_xxxx

# Mailgun (required for email)
MAILGUN_API_KEY=key-xxxx
MAILGUN_DOMAIN=mg.mathia.app

# Email settings
DEFAULT_FROM_EMAIL=noreply@mathia.app
INVOICE_FROM_EMAIL=invoices@mathia.app
```

### Django Settings

```python
# settings.py

INSTALLED_APPS = [
    # ...
    'orchestration',
]

# Invoice settings
INVOICE_MIN_AMOUNT = 100  # KES
INVOICE_MAX_AMOUNT = 10_000_000  # KES
INVOICE_AUTO_SEND_EMAIL = True
```

### Docker Compose

```yaml
services:
  web:
    environment:
      - INTASEND_PUBLIC_KEY=${INTASEND_PUBLIC_KEY}
      - INTASEND_API_KEY=${INTASEND_API_KEY}
      - MAILGUN_API_KEY=${MAILGUN_API_KEY}
      - MAILGUN_DOMAIN=${MAILGUN_DOMAIN}
```

---

## 9. Safety & Security

### Amount Validation

```python
def _validate_amount(self, amount: int) -> bool:
    """Validate invoice amount"""
    MIN_AMOUNT = 100  # KES (about $0.75)
    MAX_AMOUNT = 10_000_000  # KES (about $75,000)
    
    if amount < MIN_AMOUNT:
        raise ValueError(f"Amount too small (min: {MIN_AMOUNT})")
    if amount > MAX_AMOUNT:
        raise ValueError(f"Amount too large (max: {MAX_AMOUNT})")
    
    return True
```

### Email Verification

```python
def _validate_email(self, email: str) -> bool:
    """Validate email format"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None
```

### Authorization Check

```python
# Only invoice owner can view/edit their invoice
def get_invoice(self, invoice_id: str, user_id: int):
    invoice = Invoice.objects.get(invoice_id=invoice_id)
    if invoice.user_id != user_id:
        raise PermissionError("Not authorized")
    return invoice
```

### API Key Security

```python
# Don't log API keys
import logging
logger = logging.getLogger(__name__)

# Good
logger.info(f"Calling IntaSend API")

# Bad
logger.info(f"Calling IntaSend API with key: {INTASEND_API_KEY}")
```

---

## 10. Monitoring & Debugging

### Check Invoice Status

```bash
# Get all invoices for user
SELECT * FROM orchestration_invoice 
WHERE user_id = 42 
ORDER BY created_at DESC;

# Get unpaid invoices
SELECT * FROM orchestration_invoice 
WHERE status = 'sent' AND created_at < NOW() - INTERVAL '7 days'
ORDER BY created_at;

# Check email delivery
SELECT invoice_id, email_sent_at, email_opened_count 
FROM orchestration_invoice 
WHERE email_sent_at IS NOT NULL;
```

### Logs to Check

```bash
# Invoice creation logs
docker-compose logs web | grep -i "invoice"

# Email send logs
docker-compose logs web | grep -i "mailgun"

# Payment provider logs
docker-compose logs web | grep -i "intasend"
```

### Test Locally

```bash
# Create test invoice (no email)
curl -X POST http://localhost:8000/api/connectors/create_invoice/ \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "amount": 50000,
      "currency": "KES",
      "description": "Test invoice",
      "client_name": "Test Client",
      "client_email": "test@example.com",
      "send_email": false
    },
    "context": {"user_id": 1}
  }'
```

---

## 11. Testing

### Unit Tests

```python
# Backend/orchestration/tests.py
from django.test import TestCase
from orchestration.connectors.invoice_connector import InvoiceConnector
from django.contrib.auth.models import User

class InvoiceConnectorTests(TestCase):
    def setUp(self):
        self.connector = InvoiceConnector()
        self.user = User.objects.create(username="testuser")
    
    def test_create_invoice_success(self):
        """Test successful invoice creation"""
        result = self.connector.execute(
            parameters={
                'amount': 50000,
                'currency': 'KES',
                'description': 'Test invoice',
                'client_name': 'Test Client',
                'client_email': 'test@example.com',
                'send_email': False,
            },
            context={'user_id': self.user.id}
        )
        
        self.assertEqual(result['status'], 'success')
        self.assertIn('INV-', result['invoice_id'])
        self.assertEqual(result['amount'], 50000)
    
    def test_amount_too_small(self):
        """Test that small amounts are rejected"""
        result = self.connector.execute(
            parameters={
                'amount': 50,  # Too small
                'currency': 'KES',
                'description': 'Test',
                'client_name': 'Test',
                'client_email': 'test@example.com',
            },
            context={'user_id': self.user.id}
        )
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('too small', result['message'].lower())
    
    def test_invalid_email(self):
        """Test that invalid email is rejected"""
        result = self.connector.execute(
            parameters={
                'amount': 50000,
                'client_email': 'invalid-email',
                'description': 'Test',
                'client_name': 'Test',
            },
            context={'user_id': self.user.id}
        )
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('email', result['message'].lower())
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_invoice_workflow_integration():
    """Test invoice creation as part of workflow"""
    user = User.objects.create(username="testuser")
    workflow = UserWorkflow.objects.create(
        user=user,
        name="Invoice on Payment",
        definition={...}
    )
    
    # Trigger workflow via webhook
    response = client.post(
        f'/api/workflows/webhooks/{workflow.id}/',
        data={
            'event': 'payment.confirmed',
            'data': {
                'amount': 50000,
                'customer_email': 'client@example.com',
            }
        }
    )
    
    assert response.status_code == 202
    
    # Check that invoice was created
    invoice = Invoice.objects.get(user=user)
    assert invoice.amount == 50000
    assert invoice.status == 'sent'  # Email was sent
```

---

## 12. Known Limitations & Future Work

### Current Limitations
- ✅ No PDF generation (sends email with link, not PDF attachment)
- ✅ No custom invoice numbering sequence
- ✅ No invoice templates
- ✅ No recurring invoices
- ✅ No expense tracking

### Future Enhancements
- [ ] PDF generation and email attachment
- [ ] Custom invoice templates
- [ ] Recurring/subscription invoices
- [ ] Automatic reminder emails (overdue invoices)
- [ ] Multi-currency support with exchange rates
- [ ] Integration with accounting software (QuickBooks, Xero)
- [ ] Invoice analytics (payment rate, average days to pay)

---

## 13. Related Features & Dependencies

### Depends On
- **IntaSend API** - Payment link generation
- **Mailgun** - Email delivery
- **User Model** - Invoice owner
- **MCP Router** - Connector registration

### Used By
- **Workflows** - Payment action to create invoices
- **Chat** - Manual invoice creation from chat
- **Dashboard** - View/manage invoices

---

**Last Updated:** February 3, 2026  
**Next Review:** May 3, 2026
