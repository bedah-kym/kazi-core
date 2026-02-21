"""Workflow capabilities catalog and validation helpers."""
import json
from typing import Dict, Tuple

SYSTEM_CAPABILITIES = {
    "integrations": [
        {
            "service": "gmail",
            "description": "Send emails from a user's connected Gmail account (send-only).",
            "actions": [
                {
                    "name": "send_email",
                    "description": "Send an email",
                    "params": {
                        "to": {"type": "string", "required": True},
                        "subject": {"type": "string", "required": True},
                        "text": {"type": "string", "required": True},
                        "html": {"type": "string", "required": False},
                        "from": {"type": "string", "required": False}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "mailgun",
            "description": "Legacy alias for Gmail send-only (Mailgun is reserved for platform emails).",
            "actions": [
                {
                    "name": "send_email",
                    "description": "Send an email",
                    "params": {
                        "to": {"type": "string", "required": True},
                        "subject": {"type": "string", "required": True},
                        "text": {"type": "string", "required": True},
                        "html": {"type": "string", "required": False},
                        "from": {"type": "string", "required": False}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "whatsapp",
            "description": "Send WhatsApp messages using the system WhatsApp account.",
            "actions": [
                {
                    "name": "send_message",
                    "description": "Send a WhatsApp message",
                    "params": {
                        "phone_number": {"type": "string", "required": True},
                        "message": {"type": "string", "required": True},
                        "media_url": {"type": "string", "required": False}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "payments",
            "description": "Read-only payment insights and safe withdrawals.",
            "actions": [
                {
                    "name": "check_balance",
                    "description": "Check wallet balance",
                    "params": {}
                },
                {
                    "name": "list_transactions",
                    "description": "List recent wallet transactions",
                    "params": {
                        "limit": {"type": "integer", "required": False}
                    }
                },
                {
                    "name": "check_invoice_status",
                    "description": "Check invoice status",
                    "params": {
                        "invoice_id": {"type": "string", "required": True}
                    }
                },
                {
                    "name": "check_payments",
                    "description": "Summary of balance and recent transactions",
                    "params": {}
                },
                {
                    "name": "create_payment_link",
                    "description": "Create an IntaSend payment link",
                    "params": {
                        "amount": {"type": "number", "required": True},
                        "currency": {"type": "string", "required": False},
                        "description": {"type": "string", "required": True},
                        "phone_number": {"type": "string", "required": False},
                        "email": {"type": "string", "required": False}
                    }
                },
                {
                    "name": "withdraw",
                    "description": "Withdraw to M-Pesa (requires workflow safety policy)",
                    "params": {
                        "amount": {"type": "number", "required": True},
                        "phone_number": {"type": "string", "required": True}
                    }
                },
                {
                    "name": "check_status",
                    "description": "Check IntaSend payment status",
                    "params": {
                        "invoice_id": {"type": "string", "required": True}
                    }
                }
            ],
            "triggers": [
                {
                    "event": "payment.completed",
                    "description": "When a payment is completed",
                    "payload_fields": ["invoice_id", "amount", "email", "state"]
                },
                {
                    "event": "payment.failed",
                    "description": "When a payment fails",
                    "payload_fields": ["invoice_id", "amount", "email", "state"]
                },
                {
                    "event": "payment.updated",
                    "description": "When a payment status updates",
                    "payload_fields": ["invoice_id", "amount", "email", "state"]
                }
            ]
        },
        {
            "service": "calendly",
            "description": "Calendly scheduling and webhook triggers.",
            "actions": [
                {
                    "name": "check_availability",
                    "description": "Fetch upcoming events",
                    "params": {}
                },
                {
                    "name": "schedule_meeting",
                    "description": "Return booking link",
                    "params": {
                        "target_user": {"type": "string", "required": False}
                    }
                }
            ],
            "triggers": [
                {
                    "event": "invitee.created",
                    "description": "When a Calendly invitee is created",
                    "payload_fields": ["uri", "email", "name"]
                },
                {
                    "event": "invitee.canceled",
                    "description": "When a Calendly invitee cancels",
                    "payload_fields": ["uri", "email", "name"]
                }
            ]
        },
        {
            "service": "travel",
            "description": "Travel searches and itinerary management.",
            "actions": [
                {"name": "search_buses", "description": "Search buses", "params": {"origin": {"type": "string", "required": True}, "destination": {"type": "string", "required": True}, "travel_date": {"type": "string", "required": True}}},
                {"name": "search_hotels", "description": "Search hotels", "params": {"location": {"type": "string", "required": True}, "check_in_date": {"type": "string", "required": True}, "check_out_date": {"type": "string", "required": True}}},
                {"name": "search_flights", "description": "Search flights", "params": {"origin": {"type": "string", "required": True}, "destination": {"type": "string", "required": True}, "departure_date": {"type": "string", "required": True}}},
                {"name": "search_transfers", "description": "Search transfers", "params": {"origin": {"type": "string", "required": True}, "destination": {"type": "string", "required": True}, "travel_date": {"type": "string", "required": True}}},
                {"name": "search_events", "description": "Search events", "params": {"location": {"type": "string", "required": True}}},
                {"name": "create_itinerary", "description": "Create itinerary", "params": {}},
                {"name": "view_itinerary", "description": "View itinerary", "params": {"itinerary_id": {"type": "string", "required": False}}},
                {"name": "add_to_itinerary", "description": "Add item to itinerary", "params": {"item_type": {"type": "string", "required": True}}},
                {"name": "book_travel_item", "description": "Book itinerary item", "params": {"item_id": {"type": "string", "required": True}}}
            ],
            "triggers": []
        },
        {
            "service": "jobs",
            "description": "Job search (Upwork mock connector).",
            "actions": [
                {
                    "name": "find_jobs",
                    "description": "Search for jobs",
                    "params": {
                        "query": {"type": "string", "required": True}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "search",
            "description": "Web search via LLM.",
            "actions": [
                {
                    "name": "search_info",
                    "description": "Search for information",
                    "params": {
                        "query": {"type": "string", "required": True}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "weather",
            "description": "Weather lookup.",
            "actions": [
                {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "params": {
                        "city": {"type": "string", "required": True}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "gif",
            "description": "GIF search.",
            "actions": [
                {
                    "name": "search_gif",
                    "description": "Search GIFs",
                    "params": {
                        "query": {"type": "string", "required": True}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "currency",
            "description": "Currency conversion.",
            "actions": [
                {
                    "name": "convert_currency",
                    "description": "Convert currency",
                    "params": {
                        "amount": {"type": "number", "required": True},
                        "from_currency": {"type": "string", "required": True},
                        "to_currency": {"type": "string", "required": True}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "reminder",
            "description": "Set reminders.",
            "actions": [
                {
                    "name": "set_reminder",
                    "description": "Create reminder",
                    "params": {
                        "content": {"type": "string", "required": True},
                        "time": {"type": "string", "required": True},
                        "priority": {"type": "string", "required": False}
                    }
                }
            ],
            "triggers": []
        },
        {
            "service": "quota",
            "description": "Usage and quota checks.",
            "actions": [
                {
                    "name": "check_quotas",
                    "description": "Check user quotas",
                    "params": {}
                }
            ],
            "triggers": []
        },
        {
            "service": "schedule",
            "description": "Scheduled triggers (cron).",
            "actions": [],
            "triggers": [
                {
                    "event": "cron",
                    "description": "Cron schedule trigger",
                    "payload_fields": ["cron", "timezone"]
                }
            ]
        }
    ]
}


def get_capabilities_prompt() -> str:
    lines = [
        "You are a workflow automation assistant.",
        "Only use the services/actions listed below.",
        "Gmail is user-connected (send-only). WhatsApp is system-owned.",
        "Webhooks are service-specific only.",
        "If a workflow includes withdrawals, require a safety policy with allowed_phone_numbers and max_withdraw_amount.",
        "",
        "Available Integrations:",
        json.dumps(SYSTEM_CAPABILITIES, indent=2),
        "",
        "Output JSON ONLY in this shape:",
        "{",
        '  "assistant_message": "...",',
        '  "workflow_definition": { ... } or null,',
        '  "needs_confirmation": true|false',
        "}",
        "",
        "Workflow definition schema:",
        "{",
        '  "workflow_name": "...",',
        '  "workflow_description": "...",',
        '  "triggers": [ ... ],',
        '  "steps": [ ... ],',
        '  "policy": {"allowed_phone_numbers": [...], "max_withdraw_amount": 0} // only if withdrawals',
        "}",
    ]
    return "\n".join(lines)


def validate_workflow_definition(workflow_def: Dict) -> Tuple[bool, str]:
    if not isinstance(workflow_def, dict):
        return False, "Workflow definition must be a JSON object"

    services = {s['service']: s for s in SYSTEM_CAPABILITIES['integrations']}

    for key in ['workflow_name', 'workflow_description', 'triggers', 'steps']:
        if key not in workflow_def:
            return False, f"Missing '{key}'"

    triggers = workflow_def.get('triggers', [])
    if not isinstance(triggers, list):
        return False, "Triggers must be a list"

    for trigger in triggers:
        if not isinstance(trigger, dict):
            return False, "Each trigger must be an object"

        trigger_type = trigger.get('trigger_type')
        service = trigger.get('service')
        event = trigger.get('event')

        # Treat missing service/event/trigger_type as manual
        if (not trigger_type and not service and not event) or trigger_type == 'manual':
            continue

        # If no service is provided, treat as manual (avoid 'Unknown trigger service: None')
        if not service:
            continue

        if service == 'schedule' or event == 'cron':
            cron = trigger.get('cron') or (trigger.get('config') or {}).get('cron')
            if not cron:
                return False, "Schedule trigger requires 'cron'"
            continue

        if service not in services:
            return False, f"Unknown trigger service: {service}"

        service_def = services[service]
        valid_events = [t['event'] for t in service_def.get('triggers', [])]
        if event not in valid_events:
            return False, f"Invalid trigger event: {event}"

    steps = workflow_def.get('steps', [])
    if not isinstance(steps, list):
        return False, "Steps must be a list"

    for step in steps:
        if not isinstance(step, dict):
            return False, "Each step must be an object"
        service = step.get('service')
        action = step.get('action')

        if service not in services:
            return False, f"Unknown service: {service}"

        service_def = services[service]
        valid_actions = [a['name'] for a in service_def.get('actions', [])]
        if action not in valid_actions:
            return False, f"Invalid action: {action}"

        action_def = next((a for a in service_def.get('actions', []) if a['name'] == action), None)
        if action_def:
            required = [k for k, v in action_def.get('params', {}).items() if v.get('required')]
            params = step.get('params') or {}
            if not isinstance(params, dict):
                return False, f"Params for step '{step.get('id')}' must be an object"
            for param in required:
                if param not in params:
                    return False, f"Missing param '{param}' in step '{step.get('id')}'"

    if _requires_withdraw_policy(workflow_def):
        policy = workflow_def.get('policy') or {}
        if not policy.get('allowed_phone_numbers'):
            return False, "Withdrawals require policy.allowed_phone_numbers"
        max_amount = policy.get('max_withdraw_amount')
        if max_amount is None or max_amount == '':
            return False, "Withdrawals require policy.max_withdraw_amount"

    return True, None


def _requires_withdraw_policy(workflow_def: Dict) -> bool:
    for step in workflow_def.get('steps', []):
        if step.get('service') == 'payments' and step.get('action') == 'withdraw':
            return True
    return False
