# Mathia AI - System Stress Test & Manual Verification Plan

This document provides a set of prompts and scenarios to verify all implemented features of the Mathia AI system. Use these to ensure the orchestration, connectors, and conversational context are working as expected.

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

## 5. Travel Planning (East Africa Focus)
**Objective:** Verify the specialized travel connectors and scrapers.

*   **Bus Search:** "@mathia find me a bus from Nairobi to Mombasa for tomorrow."
*   **Hotel Search:** "@mathia I need a hotel in Diani for 3 nights next week."
*   **Flight Search:** "@mathia check flights from Nairobi to London on Dec 20."
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

---
*Updated: Jan 18, 2026*
