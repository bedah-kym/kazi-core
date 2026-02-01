# Mathia AI - System Stress Test & Manual Verification Plan

This document provides prompts and test commands to validate the current Mathia stack (chat + orchestration + workflows + webhooks/Temporal). Run them in order when possible; keep eye on logs for errors.

## 1. Core Conversational Flow & Context
**Objective:** Verify that Mathia maintains memory of the last 5 messages and follows context.

*   **Prompt 1:** "@mathia hello! My name is Alex and I'm interested in traveling to Mombasa."
*   **Prompt 2:** "@mathia what did I just say my name was?"
*   **Prompt 3:** "@mathia where did I say I wanted to go?"
*   **Prompt 4:** "@mathia tell me a joke about that place."

## 2. Intent Orchestration (MCP)
**Objective:** Verify the system correctly routes natural language to the right tool.

*   **Prompt:** "@mathia find me some Python developer jobs on Upwork." (Should trigger `find_jobs`)
*   **Prompt:** "@mathia what's the weather like in Nairobi right now?" (Should trigger `get_weather`)
*   **Prompt:** "@mathia convert 5000 KES to USD." (Should trigger `convert_currency`)
*   **Prompt:** "@mathia send me a funny cat GIF." (Should trigger `search_gif`)

## 3. Quota & Usage Limits
**Objective:** Verify the new QuotaConnector and formatting logic.

*   **Prompt:** "@mathia show my quotas."
*   **Prompt:** "@mathia how many searches do I have left?"
*   **Prompt:** "@mathia what are my usage limits?"

## 4. Payments & Financials (Read-Only)
**Objective:** Verify the Read-Only Payment Connector.

*   **Prompt:** "@mathia what is my current wallet balance?"
*   **Prompt:** "@mathia show me my last 5 transactions."
*   **Prompt:** "@mathia check the status of invoice #INV-12345."

## 5. Travel Planning (East Africa Focus, Amadeus-first)
**Objective:** Verify real data paths now that Duffel is removed and Amadeus is primary (TRAVEL_ALLOW_FALLBACK=False). Expect real results or explicit errors, not mock data.

*   **Flight Search (real):** "@mathia check flights from Nairobi to London on Dec 20."
*   **Flight Fallback Guard:** "@mathia search flights from Atlantis to Wakanda tomorrow." (Should return a graceful error, not mock data.)
*   **Hotel Search:** "@mathia I need a hotel in Diani for 3 nights next week."
*   **Itinerary:** "@mathia plan a 3-day trip to Kisumu for me."

## 6. Calendly & Scheduling
**Objective:** Verify the Calendly integration.

*   **Availability:** "@mathia is my calendar free tomorrow morning?"
*   **Booking Link:** "@mathia give me my booking link."
*   **Schedule:** "@mathia I want to schedule a meeting with @user123."

## 7. Reminders & Tasks
**Objective:** Verify the Reminder service.

*   **Prompt:** "@mathia remind me to check the server logs in 10 minutes."
*   **Prompt:** "@mathia set a high priority reminder for 'Project Deadline' at 5pm."

## 8. Document Processing (If enabled/simulated)
**Objective:** Verify document upload context.

*   **Scenario:** Upload a PDF (e.g., a travel booking).
*   **Prompt:** "@mathia what is the confirmation number in the document I just uploaded?"
*   **Prompt:** "@mathia summarize the key points of that PDF."

## 9. WhatsApp Integration (Twilio/Meta)
**Objective:** Verify the communication connectors.

*   **Prompt:** "@mathia send a WhatsApp to +254712345678 saying 'The meeting is starting now'."
*   **Prompt:** "@mathia send my invoice to +254712345678 via WhatsApp."

## 10. Security & Edge Cases
**Objective:** Verify rate limits and error handling.

*   **SQL Injection Attempt:** "@mathia search for '; DROP TABLE users; --"
*   **Rate Limit Test:** Send 11 search requests in a row. (Should trigger "Daily search limit reached").
*   **Empty Query:** "@mathia "
*   **Mixed Intent:** "@mathia what's the weather in Nairobi and also remind me to buy milk."


## 11. Workflows (chat-first, Temporal-backed)
**Objective:** Validate workflow drafting, validation, policy enforcement, and execution.

*   **Create simple workflow:** "@mathia create a workflow called 'Quota ping' that runs manually and checks my quotas." (Expect summary + approve prompt, then 'approve'.)
*   **Schedule trigger (cron):** "@mathia make a workflow 'Daily balance check' that runs every day at 09:30 Africa/Nairobi and emails - bedankimani860@gmail.com my last 5 transactions." (Should register schedule; verify in Temporal UI schedules tab.)
*   **Webhook trigger (payments):** "@mathia create a workflow 'Receipt pusher' that triggers on payment.completed and sends a mailgun email to me with the invoice_id and amount."
*   **Withdraw policy enforcement:** "@mathia create a workflow that withdraws 15000 KES to +254700000000 when payment.completed fires." (Should fail validation > WORKFLOW_WITHDRAW_MAX=10000 or missing policy.)
*   **Withdraw with policy:** "@mathia create a workflow 'Safe withdraw' with policy allowed_phone_numbers [+254700000000] max_withdraw_amount 5000, trigger payment.completed, step withdraw 3000 to +254700000000." (Should succeed.)
*   **Condition skip:** "@mathia build a workflow that runs manually, first search_flights NBO-LON Dec 20, then send_email only if price_ksh < 80000." (Ensures condition parsing.)

## 12. Webhook Endpoints (manual curl)
**Objective:** Prove signature verification and trigger wiring.

*   **Calendly webhook (signature required):**
```
curl -X POST http://localhost:8000/api/calendly/webhook/ \
  -H "X-Calendly-Signature: $(python - <<'PY'\nimport hmac,hashlib,os,json\nsecret=os.environ['CALENDLY_WEBHOOK_SIGNING_KEY'];body=json.dumps({'event':{'type':'invitee.created'},'config':{'webhook_subscription':{'owner':'https://api.calendly.com/users/ABC','uuid':'sub-123'}}});\nprint(hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest())\nPY)" \
  -H "Content-Type: application/json" \
  -d '{\"event\":{\"type\":\"invitee.created\"},\"config\":{\"webhook_subscription\":{\"owner\":\"https://api.calendly.com/users/ABC\",\"uuid\":\"sub-123\"}}}'
```
*   **IntaSend webhook (signature required):**
```
SIG=$(python - <<'PY'\nimport hmac,hashlib,os,json\nsecret=os.environ['INTASEND_WEBHOOK_SECRET'];body=json.dumps({'state':'COMPLETE','invoice_id':'INV-123','email':'admin@example.com','value':100,'fee':2});\nprint('sha256='+hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest())\nPY)
curl -X POST http://localhost:8000/payments/callback/ \
  -H "X-IntaSend-Signature: $SIG" \
  -H "Content-Type: application/json" \
  -d '{\"state\":\"COMPLETE\",\"invoice_id\":\"INV-123\",\"email\":\"admin@example.com\",\"value\":100,\"fee\":2}'
```
Expected: 401 if signature missing/invalid; triggers workflows for payment.completed when valid.

## 13. Temporal Worker & Schedules Quick Checks
**Objective:** Ensure worker + schedules are alive.

*   **Worker process:** `docker ps | grep temporal_worker` (or `docker exec mathia-project-web-1 pgrep -f temporal` if ps available).
*   **Schedule list:** Visit Temporal UI → default namespace → Schedules; confirm 'Daily balance check' appears after creation.

## 14. Regression pings (chat)
**Objective:** Confirm nothing regressed after workflow changes.

*   "@mathia send me a WhatsApp saying 'System ok' to +254712345678."
*   "@mathia check balance."
*   "@mathia search gif dancing cat."

---
*Updated: Jan 27, 2026*
