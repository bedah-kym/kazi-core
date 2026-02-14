# Temporal Workflows - Technical Specification

**Status:** ✅ Implemented (v1.0)  
**Owner:** GPT-5 (Implemented Jan 25 - Feb 3, 2026)  
**Last Updated:** February 3, 2026  
**Related Files:**
- `Backend/workflows/` (entire app)
- `Backend/workflows/models.py` (data models)
- `Backend/workflows/temporal_integration.py` (Temporal workflow definitions)
- `Backend/workflows/views.py` (REST API)
- `Backend/workflows/activity_executors.py` (service integration layer)
- `Backend/workflows/webhook_handlers.py` (external event triggers)

---

## 1. Overview

### Purpose
Temporal Workflows enable users to create sophisticated, **multi-step automations using natural language**. Instead of clicking through n8n or Zapier, users describe their needs in chat: *"When I get a payment confirmation, create an invoice in Quickbooks and email my client."* Mathia builds and executes the workflow using Temporal for durability and reliability.

### Key Capabilities
- ✅ **Natural Language Workflow Builder** - Chat-based workflow creation with Claude AI
- ✅ **Temporal Execution** - Reliable, durable workflow execution with built-in retries
- ✅ **Multi-Step Workflows** - Conditional logic, error handling, branching
- ✅ **External Triggers** - Webhooks from Calendly, IntaSend, and custom services
- ✅ **Schedule Triggers** - Cron-based and one-time scheduled workflows
- ✅ **Safety Policies** - Withdrawal limits, allowed phone numbers, spend caps
- ✅ **Dialog State** - Context-aware parameter filling across multiple messages
- ✅ **18+ Service Integrations** - Travel, payments, email, WhatsApp, scheduling, etc.
- ✅ **Real-Time Monitoring** - Temporal UI shows workflow execution progress

### Target Users
- **Solopreneurs** - Automate repetitive business tasks
- **Growth Hackers** - Create complex social/email campaigns
- **Freelancers** - Invoice clients automatically after payment
- **Teams** - Standardize workflows across organization

---

## 2. Architecture

### System Flow

```
User Chat Message
    ↓
Chat API (workflows/views.py)
    ↓
Claude AI (llm_client.py)
    ↓ (interprets intent)
Workflow Builder (workflow_agent.py)
    ↓ (validate + propose)
Workflow Approval
    ↓
Store UserWorkflow + Register Triggers
    ↓
Temporal Client (temporal_integration.py)
    ↓
Temporal Server (docker-compose.temporal.yml)
    ↓
Activity Executor (activity_executors.py)
    ↓
External Services (Amadeus, IntaSend, Mailgun, etc.)
    ↓ (result stored in WorkflowExecution)
User sees status in chat or Dashboard
```

### Key Components

#### 1. **Workflow Models** (`workflows/models.py`)

| Model | Purpose | Key Fields |
|-------|---------|-----------|
| `WorkflowDraft` | Proposed workflow pending user approval | user, definition, status, context |
| `UserWorkflow` | Active workflow definition | user, name, definition, status, execution_count |
| `WorkflowTrigger` | Registered trigger (webhook, schedule) | workflow, trigger_type, service, event, config |
| `WorkflowExecution` | Single workflow run record | workflow_id, temporal_workflow_id, status, result, trigger_data |

#### 2. **Temporal Workflow** (`workflows/temporal_integration.py`)

The `DynamicUserWorkflow` class:
- **Receives:** Workflow definition (JSON), trigger data, user context
- **Executes:** Each step as a Temporal Activity (with retries, timeouts)
- **Updates:** Execution record with results
- **Stores:** Context for conditional branching and parameter resolution

```python
@workflow.defn
class DynamicUserWorkflow:
    @workflow.run
    async def run(
        self,
        workflow_id: int,
        workflow_definition: Dict[str, Any],
        trigger_data: Dict[str, Any],
        trigger_type: str,
        execution_id: Optional[int],
        user_id: Optional[int],
    ) -> Dict[str, Any]:
        # Initialize context
        # Execute steps with conditional logic
        # Handle errors with retry policy
        # Return final result
```

#### 3. **Activity Executors** (`workflows/activity_executors.py`)

Routes workflow steps to connectors:
- **Payment actions** (`create_payment_link`, `withdraw`) - with safety policy checks
- **Travel actions** (`search_flights`, `book_hotel`) - via Amadeus
- **Communication** (`send_email`, `send_whatsapp`) - via Mailgun, WhatsApp API
- **Scheduling** (`schedule_meeting`) - via Calendly
- **Financial** (`create_invoice`) - via IntaSend

#### 4. **Webhook Handlers** (`workflows/webhook_handlers.py`)

Receives external events and triggers workflows:
- Calendly meeting scheduled → Trigger workflow
- IntaSend payment confirmed → Trigger workflow
- Custom webhooks → Trigger workflow with payload

---

## 3. Data Models

### WorkflowDraft
```python
class WorkflowDraft(models.Model):
    user = ForeignKey(User)  # Owner
    room = ForeignKey(Chatroom, null=True)  # Created in which chat room
    definition = JSONField(null=True)  # Proposed workflow definition
    context = JSONField(default=list)  # Conversation history
    status = CharField()  # draft | awaiting_confirmation | confirmed | cancelled
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
```

### UserWorkflow
```python
class UserWorkflow(models.Model):
    user = ForeignKey(User)  # Owner
    name = CharField()  # User's name for workflow
    description = TextField()  # What this workflow does
    definition = JSONField()  # Complete workflow definition (below)
    status = CharField()  # active | paused | failed | deleted
    
    # Execution tracking
    execution_count = IntegerField()  # How many times has it run
    last_executed_at = DateTimeField(null=True)  # When did it last run
    
    # Audit trail
    created_from_room = ForeignKey(Chatroom, null=True)  # Which chat created it
    created_from_draft = ForeignKey(WorkflowDraft, null=True)  # Which draft was approved
    
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
```

**Workflow Definition JSON Schema:**
```json
{
  "name": "Invoice on Payment",
  "description": "When payment received, create invoice",
  "policy": {
    "max_withdraw_amount": 50000,
    "allowed_phone_numbers": ["+254712345678"],
    "daily_spend_limit": 100000
  },
  "triggers": [
    {
      "type": "webhook",
      "service": "intersend",
      "event": "payment.confirmed",
      "config": {}
    }
  ],
  "steps": [
    {
      "id": "step_1",
      "service": "payment",
      "action": "create_invoice",
      "params": {
        "amount": "{{ trigger.amount }}",
        "client": "{{ trigger.client_id }}",
        "due_date": "2026-02-10"
      },
      "condition": "{{ trigger.amount > 1000 }}"
    },
    {
      "id": "step_2",
      "service": "email",
      "action": "send_email",
      "params": {
        "to": "client@example.com",
        "subject": "Invoice #{{ step_1.invoice_id }}",
        "body": "Your invoice is ready"
      }
    }
  ]
}
```

### WorkflowTrigger
```python
class WorkflowTrigger(models.Model):
    workflow = ForeignKey(UserWorkflow)
    trigger_type = CharField()  # webhook | schedule | manual
    service = CharField()  # intersend, calendly, custom, etc.
    event = CharField()  # payment.confirmed, meeting.scheduled, etc.
    
    # For webhooks
    config = JSONField(default=dict)  # Service-specific config
    webhook_secret = CharField(null=True)  # HMAC secret
    webhook_url = URLField(null=True)  # Where to receive events
    
    # For schedules
    schedule_cron = CharField(null=True)  # "0 9 * * MON" = every Monday 9am
    schedule_timezone = CharField(default='UTC')
    temporal_schedule_id = CharField(null=True)  # Temporal schedule ID
    
    # Metadata
    is_active = BooleanField(default=True)
    trigger_count = IntegerField(default=0)  # Times triggered
    last_triggered_at = DateTimeField(null=True)
```

### WorkflowExecution
```python
class WorkflowExecution(models.Model):
    workflow = ForeignKey(UserWorkflow)
    
    # Temporal metadata
    temporal_workflow_id = CharField(unique=True)  # UUIDv4
    temporal_run_id = CharField(null=True)
    
    # Execution details
    trigger_type = CharField()  # webhook | schedule | manual
    trigger_data = JSONField()  # Event payload that triggered workflow
    
    status = CharField()  # running | completed | failed | cancelled
    result = JSONField(null=True)  # Final output
    error_message = TextField(null=True)  # If failed
    
    started_at = DateTimeField(auto_now_add=True)
    completed_at = DateTimeField(null=True)
```

---

## 4. REST API Endpoints

### Chat-Based Workflow Builder

#### **POST** `/api/workflows/chat/`
Create or continue workflow conversation.

**Authentication:** Bearer Token (required)  
**Content-Type:** `application/json`

**Request:**
```json
{
  "message": "Create a workflow that emails me when I get a payment",
  "conversation_id": null  // Optional: continue existing conversation
}
```

**Response (200):**
```json
{
  "conversation_id": 123,
  "response": "I can help with that. What email address should I use?",
  "workflow_draft": {
    "id": 456,
    "definition": {
      "name": "Email on Payment",
      "triggers": [{"service": "intersend", "event": "payment.confirmed"}],
      "steps": [{"service": "email", "action": "send_email", "params": {...}}]
    },
    "status": "awaiting_confirmation"
  }
}
```

### Workflow Management

#### **GET** `/api/workflows/`
List all user's workflows.

**Response (200):**
```json
{
  "workflows": [
    {
      "id": 1,
      "name": "Invoice on Payment",
      "description": "Emails client invoice when payment confirmed",
      "status": "active",
      "execution_count": 47,
      "last_executed_at": "2026-02-03T14:30:00Z",
      "created_at": "2026-01-25T10:00:00Z"
    }
  ]
}
```

#### **GET** `/api/workflows/{id}/`
Get workflow details and execution history.

**Response (200):**
```json
{
  "id": 1,
  "name": "Invoice on Payment",
  "definition": { /* full workflow definition */ },
  "status": "active",
  "executions": [
    {
      "id": 999,
      "status": "completed",
      "started_at": "2026-02-03T14:30:00Z",
      "completed_at": "2026-02-03T14:30:15Z",
      "result": {"invoice_id": "INV-123", "email_sent": true}
    }
  ],
  "triggers": [
    {
      "type": "webhook",
      "service": "intersend",
      "event": "payment.confirmed",
      "webhook_url": "https://mathia.app/webhooks/workflows/1/"
    }
  ]
}
```

#### **POST** `/api/workflows/{id}/run/`
Manually trigger a workflow.

**Request:**
```json
{
  "trigger_data": {
    "amount": 50000,
    "client_id": 123
  }
}
```

**Response (202 Accepted):**
```json
{
  "execution_id": 999,
  "status": "started",
  "message": "Workflow execution started. Check status at /api/workflows/1/executions/999/"
}
```

#### **POST** `/api/workflows/{id}/pause/`
Pause an active workflow (stops receiving triggers).

#### **POST** `/api/workflows/{id}/resume/`
Resume a paused workflow.

#### **DELETE** `/api/workflows/{id}/`
Delete a workflow (archives it).

### Webhook Endpoints

#### **POST** `/api/workflows/webhooks/{workflow_id}/`
External services send events here (configured at registration).

**Header:** `X-Signature` (HMAC-256 for verification)

**Request:**
```json
{
  "event": "payment.confirmed",
  "data": {
    "payment_id": "PAY-123",
    "amount": 50000,
    "client_id": 456
  }
}
```

**Response (202 Accepted):**
```json
{
  "execution_id": 999,
  "status": "queued"
}
```

---

## 5. Usage Examples

### Example 1: Simple Payment → Invoice Workflow

**User Chat:**
```
User: "When a customer pays me through IntaSend, automatically create an invoice in Quickbooks and email them"

Claude: "I can set that up! A few questions:
1. What's your Quickbooks account email?
2. What's the email subject should I use?
3. Should I only do this for payments over a certain amount?"

User: "account@qb.com, subject is 'Invoice {{order_id}}', and only payments over 10,000"

Claude: [Proposes workflow]

User: "Yes, create it!"

System: Creates UserWorkflow, registers IntaSend webhook, starts Temporal schedule
```

**Behind the scenes:**
1. Claude parses intent from user message
2. `workflow_agent.py` validates the workflow definition
3. User approves in chat
4. `UserWorkflow` record created with definition
5. `WorkflowTrigger` created for IntaSend webhook
6. IntaSend notified to send POST to `/api/workflows/webhooks/1/`
7. Next payment triggers:
   ```json
   POST /api/workflows/webhooks/1/
   {
     "event": "payment.confirmed",
     "data": {"amount": 15000, "client": "Acme Corp"}
   }
   ```
8. `DynamicUserWorkflow` executed in Temporal:
   - Step 1: Call Quickbooks API to create invoice
   - Step 2: Send email with invoice details
9. Execution recorded in `WorkflowExecution` with status "completed"

### Example 2: Scheduled Workflow with Conditions

```json
{
  "name": "Weekly Report",
  "triggers": [
    {
      "type": "schedule",
      "schedule_cron": "0 9 ? * MON",
      "schedule_timezone": "Africa/Nairobi"
    }
  ],
  "steps": [
    {
      "id": "fetch_data",
      "service": "search",
      "action": "search_info",
      "params": {
        "query": "Weekly business metrics"
      }
    },
    {
      "id": "send_email",
      "service": "email",
      "action": "send_email",
      "params": {
        "to": "boss@company.com",
        "subject": "Weekly Report - {{ trigger.date }}",
        "body": "{{ fetch_data.results }}"
      },
      "condition": "{{ fetch_data.status == 'success' }}"
    }
  ]
}
```

Every Monday at 9 AM (Kenya time), Temporal executes this workflow.

### Example 3: Multi-Step with Error Handling

```json
{
  "name": "Book & Notify",
  "steps": [
    {
      "id": "book_flight",
      "service": "travel",
      "action": "book_travel_item",
      "params": {
        "provider_id": "AF123",
        "booking_link": "https://amadeus.com/book/AF123"
      }
    },
    {
      "id": "notify_passenger",
      "service": "whatsapp",
      "action": "send_whatsapp",
      "params": {
        "phone": "{{ trigger.passenger_phone }}",
        "message": "Your flight is booked! Confirmation: {{ book_flight.confirmation }}"
      },
      "condition": "{{ book_flight.status == 'confirmed' }}"
    },
    {
      "id": "notify_admin",
      "service": "email",
      "action": "send_email",
      "params": {
        "to": "support@mathia.app",
        "subject": "Flight booking failed",
        "body": "{{ book_flight.error }}"
      },
      "condition": "{{ book_flight.status == 'failed' }}"
    }
  ]
}
```

If flight booking fails, notify admin. If succeeds, notify passenger. Only one of the two happens.

---

## 6. Configuration & Deployment

### Environment Variables Required
```bash
# Temporal
TEMPORAL_HOST=localhost:7233  # or temporal.cloud.io:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=user-workflows

# Claude (for workflow builder chat)
ANTHROPIC_API_KEY=sk-ant-...

# External services (if using in workflows)
AMADEUS_API_KEY=...
INTASEND_PUBLIC_KEY=...
MAILGUN_API_KEY=...
CALENDLY_CLIENT_ID=...
```

### Django Settings
```python
# settings.py

INSTALLED_APPS = [
    # ...
    'workflows',  # Add workflows app
]

# Temporal configuration
TEMPORAL_HOST = os.environ.get('TEMPORAL_HOST', 'localhost:7233')
TEMPORAL_NAMESPACE = os.environ.get('TEMPORAL_NAMESPACE', 'default')
TEMPORAL_TASK_QUEUE = 'user-workflows'

# Safety limits for workflows
WORKFLOW_WITHDRAW_MAX = Decimal('100000')  # Max withdrawal per transaction
WORKFLOW_DAILY_SPEND_LIMIT = Decimal('500000')  # Max daily spend

# Allowed travel modes for booking (if using fallbacks)
TRAVEL_ALLOW_FALLBACK = False  # Use Amadeus, don't fallback to mock
```

### Docker Compose Setup

**For local development:**
```bash
docker-compose up -d temporal  # Starts Temporal server
docker-compose up -d web  # Starts Django app with worker
```

Access Temporal UI at: `http://localhost:8080`

---

## 7. Safety & Security

### Workflow Safety Policy

Workflows can be restricted with a policy:

```json
{
  "policy": {
    "max_withdraw_amount": 50000,
    "allowed_phone_numbers": ["+254712345678", "+254723456789"],
    "daily_spend_limit": 200000,
    "requires_user_confirmation": true
  }
}
```

When `withdraw` action is called:
1. Check amount ≤ `max_withdraw_amount`
2. Check phone is in `allowed_phone_numbers`
3. Check daily total ≤ `daily_spend_limit`
4. If `requires_user_confirmation`, ask user before proceeding

### Security Measures

- ✅ **Webhook Signature Verification** - HMAC-256 for all webhooks
- ✅ **Rate Limiting** - Max 10 workflow triggers per minute per user
- ✅ **User Isolation** - Workflows only accessible to owner
- ✅ **Audit Trail** - All executions logged with trigger data, result, timestamps
- ✅ **Permission Checks** - Activities validate user context
- ✅ **No Hardcoded Credentials** - All API keys from environment

---

## 8. Monitoring & Debugging

### Temporal UI
Access at `http://localhost:8080` (dev) or `https://cloud.temporal.io` (production).

Shows:
- Workflow execution status
- Step-by-step progress
- Error messages
- Retry attempts
- Timeline of events

### Application Logs

```bash
# Django logs (workflow chat, API)
docker-compose logs web | grep workflows

# Temporal worker logs
docker-compose logs worker | grep -i temporal

# Find workflow execution
SELECT * FROM workflows_workflowexecution 
WHERE workflow_id = 1 
ORDER BY started_at DESC LIMIT 10;
```

### Key Monitoring Metrics

| Metric | Alert Threshold |
|--------|-----------------|
| Workflow execution success rate | < 95% |
| Average execution time | > 30 seconds |
| Failed workflow count (daily) | > 5 |
| Temporal task timeout rate | > 1% |
| Webhook retry attempts (avg) | > 3 |

### Troubleshooting

**Problem:** Workflow not triggering
- Check `WorkflowTrigger.is_active = True`
- Check webhook URL in external service
- Check webhook logs: `SELECT * FROM django_logs WHERE message LIKE '%webhook%'`

**Problem:** Workflow execution failed
- Check `Temporal UI` → Workflow → Stack trace
- Check `WorkflowExecution.error_message`
- Check connector logs: `docker-compose logs web | grep connector_name`

**Problem:** Activity timeout
- Check activity timeout config in `temporal_integration.py`
- Check external service response time
- Consider increasing timeout or breaking into smaller steps

---

## 9. Testing

### Unit Tests

```python
# Backend/workflows/tests.py
class WorkflowModelTests(TestCase):
    def test_workflow_creation(self):
        workflow = UserWorkflow.objects.create(
            user=user,
            name="Test Workflow",
            definition={...}
        )
        self.assertEqual(workflow.status, 'active')
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_workflow_chat_to_execution():
    # Send chat message
    response = client.post('/api/workflows/chat/', {
        'message': 'Create workflow...'
    })
    
    # Verify workflow created
    workflow = UserWorkflow.objects.get(user=user)
    
    # Manually trigger
    await start_workflow_execution(workflow, trigger_data={...})
    
    # Verify execution completed
    execution = WorkflowExecution.objects.get(workflow=workflow)
    assert execution.status == 'completed'
```

### Manual Testing

Use Temporal UI to:
1. Create workflow via chat API
2. Manually trigger via `/api/workflows/{id}/run/`
3. Monitor execution in Temporal UI
4. Check results in database: `WorkflowExecution` table

---

## 10. Known Limitations & Future Work

### Current Limitations
- ✅ No sub-workflows yet (workflows can't call other workflows)
- ✅ No manual approval steps mid-workflow
- ✅ No human-in-the-loop ("pause and ask user")
- ✅ Limited to 18 integrated services

### Future Enhancements
- [ ] Sub-workflow support
- [ ] Manual approval gates
- [ ] Workflow versioning & rollback
- [ ] A/B testing workflows
- [ ] Workflow marketplace (share templates)
- [ ] Advanced debugging UI
- [ ] Workflow performance analytics

---

## 11. Related Features & Dependencies

### Depends On
- **Claude AI** (`Backend/orchestration/llm_client.py`) - For natural language understanding
- **MCP Router** (`Backend/orchestration/mcp_router.py`) - For activity execution
- **Connectors** (`Backend/orchestration/connectors/`) - All services integrated
- **Temporal Server** (docker-compose.temporal.yml) - Workflow execution engine
- **Django ORM** - Model persistence

### Related Features
- **Chat System** (`Backend/chatbot/`) - Where workflows are created
- **Travel Planning** (`Backend/travel/`) - Bookings can be part of workflows
- **Payments** (`Backend/payments/`) - Payment actions in workflows
- **Reminders** (`Backend/chatbot/tasks.py`) - Can set reminders from workflows

---

## 12. API Error Codes

| Code | Status | Meaning | Solution |
|------|--------|---------|----------|
| 400 | Bad Request | Invalid workflow definition | Check JSON schema, run validator |
| 401 | Unauthorized | Not authenticated | Provide valid Bearer token |
| 403 | Forbidden | Not workflow owner | Use your own workflows |
| 404 | Not Found | Workflow doesn't exist | Check workflow ID |
| 409 | Conflict | Workflow already exists | Use different name |
| 422 | Unprocessable | Workflow validation failed | Check definition against schema |
| 429 | Too Many Requests | Rate limited | Wait 1 minute before retrying |
| 500 | Server Error | Internal error | Check Temporal logs, contact support |
| 503 | Service Unavailable | Temporal down | Check Temporal server status |

---

**Last Updated:** February 3, 2026  
**Next Review:** May 3, 2026
