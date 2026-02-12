# 🤖 Mathia.OS: The AI Operating System for One-Person Empires

**Manage Socials, Finance, Travel, and Documents in one unified intelligence.**

Mathia is not just a workspace—it's an **AI-powered Operating System** designed for **Lone Founders, Social Media Managers, and SMEs**. It gives you the leverage of a 10-person team through a single chat interface.

---

## 🚀 Core Pillars (The "OS" Modules)

### 1. 📢 Social Media Growth Engine (New)
*Target: Social Media Managers & Growth Hackers*
*   **Zero to Viral**: AI agent that plans, posts, and monitors growth across X (Twitter), LinkedIn, and Instagram.
*   **Analytics**: Real-time feedback loops to optimize engagement.

### 2. 💰 Enterprise Finance & Quickbooks
*Target: SMEs & Freelancers*
*   **Double-Entry Ledger**: ACID-compliant financial core for handling wallet balances (Debits/Credits).
*   **Quickbooks Integration**: Automatically sync invoices and transaction data to Quickbooks.
*   **Payments**: Native IntaSend support for M-Pesa STK Pushes and Card payments.

### 3. 🧠 Productivity & Deep Notion
*Target: Lone Founders*
*   **Deep Integration**: Mathia lives inside your knowledge base. It can read/write to **Notion** pages and databases.
*   **Task Orchestration**: "Organize my week" creates tasks in Notion and schedules reminders.

### 4. ✈️ B2B Travel Planner
*Target: Travel Agents & Trip Managers*
*   **Agentic Planning**: Create detailed, day-by-day itineraries for clients.
*   **Logistics**: Manage bookings and budgets for third parties.

---

## 🛠️ Technical Stack

Mathia.OS is built for scale and real-time agentic behavior.

*   **Core**: Python 3.11, Django 5.0 (ASGI)
*   **Real-time**: Django Channels, Redis (WebSockets)
*   **AI Orchestration**: MCP Router (Model Context Protocol)
*   **Database**: PostgreSQL 16
*   **Async Tasks**: Celery & Celery Beat
*   **Frontend**: HTML5/Bootstrap (served via Django)

---

## ⚡ Quick Start (Docker)

Get your OS running in minutes.

1.  **Clone**
    ```bash
    git clone https://github.com/your-org/mathia.git
    cd mathia
    ```

2.  **Env Setup**
    ```bash
    cp .env.example .env
    # Add your API Keys (Anthropic, IntaSend, OpenWeather, etc.)
    ```

3.  **Launch**
    ```bash
    docker-compose up --build
    ```

4.  **Initialize**
    ```bash
    docker-compose exec web python Backend/manage.py migrate
    ```

Access the OS at: [http://localhost:8000](http://localhost:8000)

---

## 🧪 Testing the OS

Run the diagnostic suite to verify all pillars:

```bash
docker-compose exec web python Backend/manage.py test
docker-compose exec web python Backend/verify_ledger.py
```

---

**© 2026 Mathia Project.**
