import json
import logging
from datetime import datetime, timezone
from typing import Generator
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import POSTGRES_DB_URL, SQLITE_DB_URL

logger = logging.getLogger("founder_agent.database")

Base = declarative_base()


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id = Column(String(36), primary_key=True)
    url = Column(String(1024), nullable=False)
    status = Column(String(50), nullable=False, default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    total_founders = Column(Integer, default=0)

    founders = relationship("FounderModel", back_populates="job", cascade="all, delete-orphan")
    logs = relationship("AgentLogModel", back_populates="job", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "total_founders": self.total_founders,
            "founders": [f.to_dict() for f in self.founders] if self.founders else []
        }


class FounderModel(Base):
    __tablename__ = "founders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(36), ForeignKey("extraction_jobs.id"), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(255), nullable=True)
    bio = Column(Text, nullable=True)
    previous_experience = Column(Text, nullable=True)
    linkedin_url = Column(String(1024), nullable=True)
    evidence_json = Column(Text, nullable=True)  # JSON array string
    sources_json = Column(Text, nullable=True)   # JSON array string
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    job = relationship("ExtractionJob", back_populates="founders")

    def to_dict(self):
        evidence_list = []
        sources_list = []
        try:
            if self.evidence_json:
                evidence_list = json.loads(self.evidence_json)
        except Exception:
            evidence_list = [self.evidence_json] if self.evidence_json else []

        try:
            if self.sources_json:
                sources_list = json.loads(self.sources_json)
        except Exception:
            sources_list = [self.sources_json] if self.sources_json else []

        return {
            "id": self.id,
            "job_id": self.job_id,
            "name": self.name,
            "role": self.role,
            "bio": self.bio,
            "previous_experience": self.previous_experience,
            "linkedin_url": self.linkedin_url,
            "evidence": evidence_list,
            "sources": sources_list,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class AgentLogModel(Base):
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(36), ForeignKey("extraction_jobs.id"), nullable=False)
    step_type = Column(String(50), nullable=False)  # TOOL_CALL, TOOL_RESULT, INFO, ERROR, MERGE
    message = Column(Text, nullable=False)
    details_json = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    job = relationship("ExtractionJob", back_populates="logs")

    def to_dict(self):
        details = None
        if self.details_json:
            try:
                details = json.loads(self.details_json)
            except Exception:
                details = self.details_json
        return {
            "id": self.id,
            "job_id": self.job_id,
            "step_type": self.step_type,
            "message": self.message,
            "details": details,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


# Database connection factory with PostgreSQL primary & SQLite fallback
def get_engine():
    pg_url = POSTGRES_DB_URL
    if pg_url.startswith("postgresql://"):
        pg_url = pg_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    
    try:
        engine = create_engine(pg_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        # Test connection
        with engine.connect() as conn:
            pass
        logger.info(f"Connected to PostgreSQL database at {pg_url}")
        return engine, "PostgreSQL"
    except Exception as e:
        logger.warning(f"Could not connect to PostgreSQL ({e}). Falling back to SQLite at {SQLITE_DB_URL}")
        engine = create_engine(SQLITE_DB_URL, connect_args={"check_same_thread": False})
        return engine, "SQLite"


db_engine, DB_TYPE = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


def init_db():
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=db_engine)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
