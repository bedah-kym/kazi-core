# Use Cases

Kazi is general-purpose, but it earns its keep in a few specific spots. These
are the ones we keep in mind when we build.

## 1. A WhatsApp assistant for your business

Customers message the business number; the agent answers from your playbook,
files new contacts automatically, and sends order confirmations or reminders
over WhatsApp, email, or in-app — whichever the customer prefers.

Uses: the WhatsApp and notifications connectors, the contact system, and agent
memory.

## 2. Travel with a paper trail

*"Book a flight to Mombasa, find a hotel near the beach, and email my boss the
itinerary."* The agent plans the multi-step run, verifies it, and executes it —
pausing for your approval before anything is actually booked or charged.

Uses: the travel module (Amadeus), workflows with human checkpoints, and action
receipts.

## 3. An ops inbox that waits for you

High-risk actions never run on autopilot. A workflow pauses, notifies an
operator, and resumes only when a human says go. Perfect for finance, ops, or
anything where "the model decided" isn't a good enough answer.

Uses: durable checkpoints, the operator API, and notifications.

## 4. Payments for a Kenyan SME

M-Pesa and card payments through IntaSend, a double-entry ledger that does real
debits and credits, and recurring invoices with dispute tracking. The books
stay honest because the ledger is, too.

Uses: the payments module and the quota system.

## The thread through all four

Same three ideas every time:

- **The agent remembers** — so context survives across conversations.
- **Humans stay in the loop** — so nothing expensive or irreversible runs blind.
- **Everything leaves a receipt** — so you can audit and undo.

If your idea doesn't fit one of these, [add a connector](add-a-connector.md) —
that's the extension point, and it's one file.
