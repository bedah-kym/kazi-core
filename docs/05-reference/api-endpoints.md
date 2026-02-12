# API Endpoints Reference - Complete Guide

**Status:** ✅ Updated (v2.0 - Feb 3, 2026)  
**Last Updated:** February 3, 2026  
**Scope:** All REST API endpoints across Mathia platform  
**Related:**
- `Backend/Backend/urls.py` (root URL configuration)
- `Backend/Api/urls.py` (API v1 routes)
- `Backend/workflows/urls.py` (workflow endpoints)
- `Backend/chatbot/urls.py` (chat endpoints)
- `Backend/orchestration/views.py` (connector endpoints)

---

## Quick Navigation

- [Authentication](#authentication)
- [Chat & Messaging](#chat--messaging)
- [Workflows](#workflows)
- [Connectors](#connectors)
- [Travel](#travel)
- [Payments](#payments)
- [User Management](#user-management)
- [Error Codes](#error-codes)

---

## Authentication

All endpoints (except `/auth/`) require a Bearer token in the `Authorization` header.

**Header Format:**
```
Authorization: Bearer <your_access_token>
```

### Get Access Token

#### **POST** `/api/auth/token/`

**Request:**
```json
{
  "username": "user@example.com",
  "password": "your_password"
}
```

**Response (200):**
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": 42,
    "username": "user@example.com",
    "name": "John Doe"
  }
}
```

---

## Chat & Messaging

### Chat Rooms

#### **GET** `/api/chatrooms/`
List all chat rooms for current user.

**Query Parameters:**
- `search` - Filter by room name
- `limit` - Results per page (default: 20)
- `offset` - Pagination offset

**Response (200):**
```json
{
  "count": 3,
  "next": "/api/chatrooms/?offset=20",
  "previous": null,
  "results": [
    {
      "id": "room_123",
      "name": "Travel Planning",
      "created_at": "2026-02-01T10:00:00Z",
      "last_message_at": "2026-02-03T14:30:00Z",
      "participant_count": 2,
      "is_encrypted": true
    }
  ]
}
```

#### **POST** `/api/chatrooms/`
Create a new chat room.

**Request:**
```json
{
  "name": "Trip to London",
  "is_encrypted": true
}
```

**Response (201):**
```json
{
  "id": "room_456",
  "name": "Trip to London",
  "created_at": "2026-02-03T15:00:00Z"
}
```

#### **GET** `/api/chatrooms/{id}/`
Get specific chat room details.

#### **DELETE** `/api/chatrooms/{id}/`
Delete a chat room and all messages.

### Messages

#### **GET** `/api/chatrooms/{id}/messages/`
List messages in a chat room with threading support.

**Query Parameters:**
- `limit=50` - Messages per page
- `offset=0` - Pagination offset
- `thread_id` - Get specific thread (optional)
- `flat=true` - Return flat list instead of threaded (for backward compatibility)

**Response (200):**
```json
{
  "count": 145,
  "next": "/api/chatrooms/room_123/messages/?offset=50",
  "previous": null,
  "results": [
    {
      "id": 100,
      "content": "Find flights from Nairobi to London",
      "sender": {
        "id": 42,
        "name": "John"
      },
      "created_at": "2026-02-03T14:00:00Z",
      "is_bot": false,
      "parent": null,
      "parent_info": null,
      "thread_depth": 0,
      "replies": [
        {
          "id": 101,
          "content": "Here are the options...",
          "sender": {"id": 1, "name": "Claude"},
          "parent": 100,
          "thread_depth": 1,
          "replies": []
        }
      ]
    }
  ]
}
```

#### **POST** `/api/chatrooms/{id}/messages/`
Send a message (or reply to a message).

**Request:**
```json
{
  "content": "Show me business class options",
  "parent_id": 100  // Optional: reply to message 100
}
```

**Response (201):**
```json
{
  "id": 102,
  "content": "Show me business class options",
  "sender": {"id": 42, "name": "John"},
  "parent_id": 100,
  "thread_depth": 2,
  "created_at": "2026-02-03T14:05:00Z"
}
```

#### **GET** `/api/chatrooms/{id}/messages/{msg_id}/thread/`
Get a complete message thread (message + all replies).

**Response (200):**
```json
{
  "root_message": {
    "id": 100,
    "content": "Find flights..."
  },
  "thread": [
    {"id": 100, "content": "Find flights...", "thread_depth": 0},
    {"id": 101, "content": "Here are options...", "thread_depth": 1},
    {"id": 102, "content": "Show business class", "thread_depth": 2}
  ],
  "reply_count": 2
}
```

---

## Workflows

### List & Manage Workflows

#### **GET** `/api/workflows/`
List all workflows for current user.

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
  "description": "...",
  "status": "active",
  "definition": {
    "name": "Invoice on Payment",
    "triggers": [...],
    "steps": [...]
  },
  "executions": [
    {
      "id": 999,
      "status": "completed",
      "started_at": "2026-02-03T14:30:00Z",
      "completed_at": "2026-02-03T14:30:15Z",
      "result": {"invoice_id": "INV-123"}
    }
  ],
  "triggers": [
    {
      "id": 1,
      "type": "webhook",
      "service": "intersend",
      "event": "payment.confirmed",
      "webhook_url": "https://mathia.app/webhooks/workflows/1/",
      "is_active": true
    }
  ]
}
```

#### **POST** `/api/workflows/{id}/run/`
Manually execute a workflow.

**Request:**
```json
{
  "trigger_data": {
    "amount": 50000,
    "client_id": 123,
    "client_email": "client@example.com"
  }
}
```

**Response (202 Accepted):**
```json
{
  "execution_id": 999,
  "status": "started",
  "message": "Workflow execution started"
}
```

#### **POST** `/api/workflows/{id}/pause/`
Pause a workflow (stops receiving triggers).

**Response (200):**
```json
{
  "id": 1,
  "status": "paused"
}
```

#### **POST** `/api/workflows/{id}/resume/`
Resume a paused workflow.

#### **DELETE** `/api/workflows/{id}/`
Delete (archive) a workflow.

### Workflow Chat Builder

#### **POST** `/api/workflows/chat/`
Create or continue workflow via natural language chat.

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
  "response": "I can help with that! What email address should I send to?",
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

#### **POST** `/api/workflows/chat/{conversation_id}/confirm/`
Approve a workflow proposal from chat builder.

**Response (201):**
```json
{
  "workflow_id": 1,
  "status": "active",
  "message": "Workflow created and activated"
}
```

---

## Connectors

### Execute Connector Action

#### **POST** `/api/connectors/{action}/`

Execute any registered connector action directly (without workflow).

**Common Actions:**
- `search_flights`
- `search_hotels`
- `send_email`
- `send_whatsapp`
- `create_invoice`
- `send_money`
- `search_info`
- `schedule_meeting`

### Travel Connectors

#### **POST** `/api/connectors/search_flights/`
Search for available flights.

**Request:**
```json
{
  "parameters": {
    "from": "Nairobi",
    "to": "London",
    "departure_date": "2026-02-10",
    "return_date": "2026-02-20",
    "passenger_count": 1,
    "cabin_class": "economy"
  },
  "context": {"user_id": 42, "room_id": "room_123"}
}
```

**Response (200):**
```json
{
  "status": "success",
  "flights": [
    {
      "id": "AF123",
      "airline": "Kenya Airways",
      "departure_time": "10:30",
      "arrival_time": "20:45",
      "duration": "10h 15m",
      "stops": 0,
      "price": 180000,
      "currency": "KES"
    }
  ]
}
```

#### **POST** `/api/connectors/search_hotels/`
Search for available hotels.

**Request:**
```json
{
  "parameters": {
    "city": "London",
    "check_in": "2026-02-10",
    "check_out": "2026-02-20",
    "guest_count": 1,
    "room_type": "single"
  },
  "context": {"user_id": 42}
}
```

**Response (200):**
```json
{
  "status": "success",
  "hotels": [
    {
      "id": "HT456",
      "name": "The Savoy London",
      "rating": 4.8,
      "price_per_night": 25000,
      "currency": "KES",
      "amenities": ["WiFi", "Gym", "Pool"]
    }
  ]
}
```

#### **POST** `/api/connectors/book_travel_item/`
Book a flight, hotel, or transfer (payment integration).

**Request:**
```json
{
  "parameters": {
    "item_type": "flight",
    "provider_id": "AF123",
    "booking_link": "https://amadeus.com/book/AF123"
  },
  "context": {"user_id": 42}
}
```

**Response (200):**
```json
{
  "status": "confirmed",
  "confirmation": "BK-123456",
  "receipt_url": "https://..."
}
```

### Payment Connectors

#### **POST** `/api/connectors/send_money/`
Send payment to a recipient.

**Request:**
```json
{
  "parameters": {
    "amount": 5000,
    "recipient": "+254712345678",
    "currency": "KES",
    "description": "Payment for services"
  },
  "context": {"user_id": 42}
}
```

**Response (200):**
```json
{
  "status": "success",
  "transaction_id": "TXN-123",
  "amount": 5000,
  "recipient": "+254712345678"
}
```

#### **POST** `/api/connectors/create_invoice/`
Create an invoice.

**Request:**
```json
{
  "parameters": {
    "amount": 50000,
    "currency": "KES",
    "description": "Website redesign",
    "client_name": "Acme Corp",
    "client_email": "accounts@acme.com",
    "send_email": true,
    "payment_provider": "intasend"
  },
  "context": {"user_id": 42}
}
```

**Response (201):**
```json
{
  "status": "success",
  "invoice_id": "INV-2026-12345",
  "amount": 50000,
  "payment_link": "https://intasend.com/pay/...",
  "email_sent": true
}
```

### Communication Connectors

#### **POST** `/api/connectors/send_email/`
Send an email.

**Request:**
```json
{
  "parameters": {
    "to": "client@example.com",
    "subject": "Invoice #INV-123",
    "body": "Please find your invoice attached"
  },
  "context": {"user_id": 42}
}
```

**Response (200):**
```json
{
  "status": "success",
  "message_id": "MSG-123",
  "recipient": "client@example.com"
}
```

#### **POST** `/api/connectors/send_whatsapp/`
Send a WhatsApp message.

**Request:**
```json
{
  "parameters": {
    "phone": "+254712345678",
    "message": "Hi John, your invoice is ready!"
  },
  "context": {"user_id": 42}
}
```

**Response (200):**
```json
{
  "status": "success",
  "message_id": "WA-123",
  "phone": "+254712345678"
}
```

### Search & Information

#### **POST** `/api/connectors/search_info/`
Search for general information (powered by Claude AI).

**Request:**
```json
{
  "parameters": {
    "query": "What's the weather in London next week?"
  },
  "context": {"user_id": 42}
}
```

**Response (200):**
```json
{
  "status": "success",
  "result": "Based on current forecasts, London will be...",
  "sources": ["weather.com", "met.office.gov.uk"]
}
```

---

## Travel

### Manage Travel Plans

#### **GET** `/api/travel/bookings/`
List all travel bookings for current user.

**Response (200):**
```json
{
  "bookings": [
    {
      "id": 1,
      "type": "flight",
      "from": "Nairobi",
      "to": "London",
      "date": "2026-02-10",
      "status": "confirmed",
      "confirmation": "AF123",
      "booked_at": "2026-01-25T10:00:00Z"
    }
  ]
}
```

#### **GET** `/api/travel/bookings/{id}/`
Get booking details.

#### **POST** `/api/travel/bookings/`
Create new booking (via workflow).

#### **DELETE** `/api/travel/bookings/{id}/`
Cancel a booking.

---

## Payments

### Manage Payments

#### **GET** `/api/payments/transactions/`
List all transactions for current user.

**Query Parameters:**
- `status` - pending, completed, failed
- `from_date` - Filter by date range
- `to_date`

**Response (200):**
```json
{
  "transactions": [
    {
      "id": 1,
      "type": "sent",
      "amount": 5000,
      "recipient": "+254712345678",
      "status": "completed",
      "created_at": "2026-02-03T14:00:00Z"
    }
  ]
}
```

#### **GET** `/api/payments/wallet/`
Get current wallet balance.

**Response (200):**
```json
{
  "balance": 150000,
  "currency": "KES",
  "last_updated": "2026-02-03T15:00:00Z"
}
```

#### **POST** `/api/payments/topup/`
Top up wallet.

**Request:**
```json
{
  "amount": 50000,
  "payment_method": "card"
}
```

---

## User Management

### Profile

#### **GET** `/api/users/profile/`
Get current user profile.

**Response (200):**
```json
{
  "id": 42,
  "email": "john@example.com",
  "name": "John Doe",
  "phone": "+254712345678",
  "created_at": "2025-01-01T00:00:00Z",
  "profile_complete": true
}
```

#### **PUT** `/api/users/profile/`
Update user profile.

**Request:**
```json
{
  "name": "John Doe",
  "phone": "+254712345678"
}
```

### Preferences

#### **GET** `/api/users/preferences/`
Get user preferences (notifications, language, etc.)

#### **PUT** `/api/users/preferences/`
Update preferences.

**Request:**
```json
{
  "language": "en",
  "notifications_enabled": true,
  "timezone": "Africa/Nairobi"
}
```

---

## Webhook Endpoints (Inbound)

### Workflow Webhooks

#### **POST** `/api/workflows/webhooks/{workflow_id}/`
External services send events here to trigger workflows.

**Header:** `X-Signature: sha256=...` (HMAC verification)

**Request:**
```json
{
  "event": "payment.confirmed",
  "data": {
    "payment_id": "PAY-123",
    "amount": 50000,
    "customer_name": "John"
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

### Service Webhooks

#### **POST** `/webhooks/intasend/`
IntaSend sends payment confirmations here.

#### **POST** `/webhooks/calendly/`
Calendly sends meeting confirmations here.

---

## Error Codes

### HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | OK | Successful GET, PUT |
| 201 | Created | Successful POST that creates resource |
| 202 | Accepted | Async operation started (workflow trigger) |
| 400 | Bad Request | Invalid parameters, validation error |
| 401 | Unauthorized | Missing or invalid Bearer token |
| 403 | Forbidden | Not authorized (e.g., not workflow owner) |
| 404 | Not Found | Resource doesn't exist |
| 422 | Unprocessable | Invalid input data (validation error) |
| 429 | Too Many Requests | Rate limited |
| 500 | Server Error | Internal server error |
| 503 | Service Unavailable | Temporal down, Redis down, etc. |

### Error Response Format

**All error responses follow this format:**

```json
{
  "status": "error",
  "message": "Human-readable error message",
  "error_code": "SPECIFIC_ERROR_CODE",
  "details": {
    "field": "amount",
    "issue": "Amount must be between 100 and 10000000"
  }
}
```

### Common Errors

| Error Code | HTTP | Meaning | Fix |
|-----------|------|---------|-----|
| `INVALID_TOKEN` | 401 | Token missing or invalid | Provide valid Bearer token |
| `TOKEN_EXPIRED` | 401 | Token expired | Request new token via `/api/auth/token/` |
| `INVALID_PARAMS` | 400 | Missing or invalid parameters | Check request body against schema |
| `RESOURCE_NOT_FOUND` | 404 | Resource doesn't exist | Check ID is correct |
| `PERMISSION_DENIED` | 403 | Not authorized for this resource | Verify ownership |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests | Wait 1 minute before retrying |
| `SERVICE_UNAVAILABLE` | 503 | External service down | Check service status, retry later |
| `CONNECTOR_ERROR` | 500 | Connector execution failed | Check connector logs, verify API keys |

---

## Rate Limiting

### Limits by Endpoint Category

| Category | Limit | Window |
|----------|-------|--------|
| Chat messages | 50 | per minute |
| Workflow execution | 100 | per hour |
| Travel searches | 30 | per minute |
| Payment requests | 10 | per minute |
| Create workflows | 5 | per hour |
| API calls (general) | 1000 | per hour |

**Headers in Response:**
```
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1707129060
```

---

## Pagination

### Standard Pagination

**Query Parameters:**
```
GET /api/chatrooms/?limit=20&offset=40
```

**Response:**
```json
{
  "count": 150,
  "next": "/api/chatrooms/?limit=20&offset=60",
  "previous": "/api/chatrooms/?limit=20&offset=20",
  "results": [...]
}
```

---

## WebSocket Endpoints

### Real-Time Chat

#### **WebSocket** `/ws/chat/{room_id}/`

**Connection:**
```javascript
const socket = new WebSocket(
  `wss://mathia.app/ws/chat/room_123/?token=YOUR_TOKEN`
);
```

**Send Message:**
```json
{
  "type": "chat.message",
  "message": "Find flights",
  "parent_id": null
}
```

**Receive Message:**
```json
{
  "type": "chat.message",
  "id": 100,
  "message": "Find flights",
  "sender": "John",
  "sender_id": 42,
  "parent_id": null,
  "thread_depth": 0,
  "created_at": "2026-02-03T14:00:00Z"
}
```

---

**Last Updated:** February 3, 2026  
**Next Review:** May 3, 2026
