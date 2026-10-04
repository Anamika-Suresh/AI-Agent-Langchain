# AI Founder Extraction Platform 🚀

A full-stack AI Agent web application built to extract company founders automatically. It uses a **LangChain** autonomous browser agent, **Playwright**, **FastAPI**, **PostgreSQL (via SQLAlchemy)**, and **WebSockets** for streaming live terminal updates and real-time founder discoveries.

---

## 🌟 Key Features

1. **Frontend UI (HTML5, Vanilla CSS, JS)**:
   - **Glassmorphism Dark Aesthetics**: Modern dark theme with cyan, purple, and emerald glowing gradients and custom micro-animations.
   - **Extraction Console**: Real-time URL input, quick presets, live status indicators, terminal log console, and dynamic founder card cards.
   - **Founders Directory**: Searchable, filterable table of all founders stored across PostgreSQL database records with LinkedIn profile badges, bios, evidence quotes, and sources.
   - **Job History**: Historical extraction log with status badges (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`), execution timestamps, JSON/CSV exports, and detailed modal drawer.
   - **Analytics Dashboard**: Real-time metrics overview (Total Scraped Sites, Total Founders Extracted, Success Rate, Active Runs, Database Engine).

2. **Backend API (FastAPI)**:
   - RESTful API endpoints for creating extraction jobs, querying past jobs, retrieving detailed logs, searching founders, deleting records, and exporting data in JSON/CSV formats.

3. **Database Layer (PostgreSQL with SQLAlchemy)**:
   - Built with SQLAlchemy ORM supporting **PostgreSQL** out of the box (`postgresql://user:pass@localhost:5432/dbname`).
   - Includes automatic graceful fallback to local SQLite (`sqlite:///./founder_agent.db`) if PostgreSQL credentials are not configured.

4. **Real-Time WebSocket Streaming**:
   - Bi-directional WebSocket server at `/ws/extract/{job_id}` streaming live step logs, tool invocations (`open_page`, `click_text`, `extract_founders`), and founder details as they are discovered.

---

## 📁 Architecture Overview

```
c:\Projects\AI_Agent\
├── config.py           # Environment variables (DB URL, LLM model, Ports)
├── database.py         # SQLAlchemy engine setup (PostgreSQL primary + SQLite fallback) & Models
├── agent_service.py    # AI Founder Extraction Runner with live WebSocket event emitting
├── main.py             # FastAPI Server with REST routes, WebSocket manager & static serving
├── AI_Agent.py         # Original agent script
├── static/             # Frontend Application
│   ├── index.html      # Responsive Single Page Application
│   ├── styles.css      # Custom design tokens, glassmorphism, responsive CSS
│   └── app.js          # Interactive frontend logic, WebSocket client, tables
├── .env.example        # Environment variable template
└── README.md           # Documentation
```

---

## 🛠️ Getting Started

### 1. Prerequisites
- Python 3.10+
- (Optional) PostgreSQL server running on `localhost:5432`

### 2. Installation

Install all required Python dependencies:
```bash
pip install fastapi uvicorn[standard] sqlalchemy psycopg2-binary asyncpg websockets python-dotenv langchain langchain-groq playwright
playwright install chromium
```

### 3. Environment Configuration

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Edit `.env` to set your PostgreSQL connection string and LLM API Key:
```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/founder_db
LLM_MODEL=groq:openai/gpt-oss-120b
GROQ_API_KEY=your_groq_api_key_here
HEADLESS=True
PORT=8000
HOST=0.0.0.0
```

*Note: If PostgreSQL is not running or credentials fail, the application will automatically fall back to SQLite without crashing.*

### 4. Running the Web Application

Start the FastAPI application with Uvicorn:
```bash
python main.py
```
Or with Uvicorn directly:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser at: **`http://localhost:8000`**

---

## 🔌 API Endpoints Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the web app UI |
| `POST` | `/api/extract` | Create and trigger an extraction job (`{"url": "https://..."}`) |
| `GET` | `/api/jobs` | Get list of all extraction jobs |
| `GET` | `/api/jobs/{job_id}` | Get job details, step logs, and extracted founders |
| `DELETE` | `/api/jobs/{job_id}` | Delete a job record and its extracted data |
| `GET` | `/api/founders` | Search founders database (`?q=keyword`) |
| `GET` | `/api/stats` | System & database status metrics |
| `GET` | `/api/export/{job_id}` | Export job results (`?format=json` or `?format=csv`) |
| `WS` | `/ws/extract/{job_id}` | Real-time WebSocket event stream |
