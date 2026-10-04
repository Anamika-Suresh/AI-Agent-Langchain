import os
from dotenv import load_dotenv

load_dotenv()

# Database configuration: defaults to PostgreSQL, falls back smoothly to SQLite if needed
POSTGRES_DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/founder_agent")
SQLITE_DB_URL = "sqlite:///./founder_agent.db"

# LLM & Browser config
DEFAULT_URL = os.getenv("DEFAULT_URL", "https://globalwebproduction.com/")
LLM_MODEL = os.getenv("LLM_MODEL", "groq:openai/gpt-oss-120b")
MAX_CHARS = int(os.getenv("MAX_CHARS", "12000"))
HEADLESS = os.getenv("HEADLESS", "True").lower() in ("true", "1", "t")
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "127.0.0.1")
