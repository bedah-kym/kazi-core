# 🤖 Mathia: AI-Powered Enterprise Assistant Platform

**The Intelligent Workspace for Modern Business Operations**

Mathia is a next-generation enterprise platform that combines real-time communication, AI-driven automation, and financial management into a single, unified workspace. It empowers users to manage travel, payments, and daily tasks through a conversational interface.

---

## 💼 Business Overview (For Investors)

Mathia solves the fragmentation problem in modern tooling by integrating three critical business pillars:

### 1. **Intelligent Assistance**
Instead of just a chatbot, Mathia is an **agent**. It connects to your calendar, email, and external tools to perform actions like "Schedule a meeting with John" or "Find a flight to Nairobi".
*   **Key Value**: Reduces context switching and automates routine administrative tasks.

### 2. **Enterprise Fintech & Payments**
A robust, ledger-based financial system built directly into the chat.
*   **Transactions**: Seamlessly send money, generate invoices, and handle subscriptions without leaving the app.
*   **Compliance**: Built on a Double-Entry Ledger system (ACID compliant) ensuring financial data integrity.
*   **Monetization**: Integrated Platform Fees and subscription models ready for scale.
*   **Gateway**: Native integration with **IntaSend** (M-Pesa, Card) for African markets.

### 3. **AI Travel Planning**
A dedicated module for end-to-end trip management.
*   **Itineraries**: AI generates detailed day-by-day travel plans based on user preferences.
*   **Booking**: Integrated budget tracking and status updates.

---

## 🛠️ Technical Overview (For Developers)

Mathia is built on a modern, scalable stack designed for high concurrency and real-time interaction.

### Tech Stack
*   **Backend**: Python 3.11, Django 5.0 (ASGI)
*   **Real-time**: Django Channels, Redis (WebSockets)
*   **Database**: PostgreSQL 16
*   **Task Queue**: Celery & Celery Beat (Redis Broker)
*   **AI Engine**: Anthropic Claude 3.5 Sonnet / Hugging Face Fallback
*   **Frontend**: HTML5, Bootstrap 5.3, Vanilla JS (No heavy framework bloat)
*   **Infrastructure**: Docker & Docker Compose

### Key Modules

#### 1. **Orchestration Layer (`/orchestration`)**
The brain of the system. It parses natural language intents and routes them to specific "Connectors".
*   `MCPRouter`: Central hub for intent routing.
*   `Connectors`: Modular plugins (e.g., `PaymentConnector`, `CalendlyConnector`) that execute safe actions.

#### 2. **Enterprise Payments (`/payments`)**
A fully ACID-compliant financial system.
*   **Double-Entry Ledger**: Every transaction has equal Debits and Credits.
*   **Models**: `LedgerAccount`, `JournalEntry`, `PaymentRequest` (Invoice).
*   **Security**: AI has strictly **Read-Only** access to financial data.

#### 3. **Unified Quota System (`/users`)**
Fair usage enforcement across all system resources.
*   Limits tracked for: AI Actions, Messages, Searches, and Uploads.
*   Visualized via real-time WebSocket updates to the frontend.

#### 4. **Chat & Context (`/chatbot`)**
*   **Streaming**: Character-by-character AI responses.
*   **Memory**: Vector-like context retention for personalized interactions.

---

## 🚀 Quick Start Guide

We use **Docker** to make setup effortless. You don't need to install Python or Postgres locally.

### Prerequisites
*   Docker Desktop installed and running.
*   Git.

### Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/your-org/mathia.git
    cd mathia
    ```

2.  **Environment Setup**
    Copy the example env file:
    ```bash
    cp .env.example .env
    ```
    *Update `.env` with your API keys (Anthropic, IntaSend, etc.) if you have them.*

3.  **Launch via Docker**
    ```bash
    docker-compose up --build
    ```
    *This starts the Web, Database, Redis, and Celery services.*

4.  **Initialize Database**
    Open a new terminal and run:
    ```bash
    docker-compose exec web python Backend/manage.py migrate
    ```

5.  **Access the App**
    *   **App**: [http://localhost:8000](http://localhost:8000)
    *   **Admin**: [http://localhost:8000/admin](http://localhost:8000/admin) (Create a superuser first: `docker-compose exec web python Backend/manage.py createsuperuser`)

---

## 🧪 Running Tests

We prioritize reliability. Run the full test suite (including payment ledger verification) with:

```bash
# Run Django Tests
docker-compose exec web python Backend/manage.py test

# Verify Payment Ledger Logic (Double-Entry check)
docker-compose exec web python Backend/verify_ledger.py
```

---

## 🤝 Contributing

We welcome contributions! Please follow these steps:
1.  Fork the repo.
2.  Create a feature branch (`git checkout -b feature/amazing-feature`).
3.  Commit your changes.
4.  Push to the branch.
5.  Open a Pull Request.

---

**© 2026 Mathia Project. All Rights Reserved.**
