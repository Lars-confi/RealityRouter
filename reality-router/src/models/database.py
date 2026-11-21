"""
Database models for LLM routing system
"""

import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Create the base class for database models
Base = declarative_base()


class RoutingLog(Base):
    """Database model for routing logs"""

    __tablename__ = "routing_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    query = Column(Text, nullable=False)
    agent_id = Column(String(100))
    model_id = Column(String(100), nullable=False)
    model_name = Column(String(100), nullable=False)
    expected_utility = Column(Float, nullable=False)
    cost = Column(Float, nullable=False)
    time = Column(Float, nullable=False)
    probability = Column(Float, nullable=False)
    success = Column(Boolean, default=True)
    response_text = Column(Text)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)
    request_payload = Column(Text)
    response_payload = Column(Text)
    routing_context = Column(Text)
    features_json = Column(Text)  # Stores the fixed-dimensional agent feature set
    user_sentiment = Column(String(20))  # unhappy, indeterminate, happy
    strategy = Column(String(50))  # expected_utility or tiered_assessment
    reality_check_id = Column(String(100))  # Linked ID from Reality Router API
    confidence = Column(Float)
    entropy = Column(Float)
    logprobs_mean = Column(Float)
    logprobs_std = Column(Float)
    first_token_logprob = Column(Float)
    first_token_top_logprobs = Column(Text)
    second_token_logprob = Column(Float)
    second_token_top_logprobs = Column(Text)
    potential_cost = Column(Float)

    def __repr__(self):
        return f"<RoutingLog(id={self.id}, model={self.model_name}, timestamp={self.timestamp})>"


class ModelPerformance(Base):
    """Database model for model performance metrics"""

    __tablename__ = "model_performance"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String(100), unique=True, nullable=False)
    model_name = Column(String(100), nullable=False)
    total_requests = Column(Integer, default=0)
    total_cost = Column(Float, default=0.0)
    average_time = Column(Float, default=0.0)
    success_rate = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ModelPerformance(id={self.id}, model={self.model_name})>"


# Database setup
# Use the database in the project root (one level up from src/)
APP_HOME = os.getenv("REALITY_ROUTER_HOME", os.path.expanduser("~/.reality_router"))
os.makedirs(APP_HOME, exist_ok=True)
DEFAULT_DB_PATH = os.path.join(APP_HOME, "reality_router.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

def configure_sqlite_engine(target_engine):
    """Apply SQLite concurrency pragmas (WAL, busy_timeout, synchronous=NORMAL) on connect."""
    if target_engine.dialect.name != "sqlite":
        return

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in DATABASE_URL else {},
)
configure_sqlite_engine(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine=None):
    """Initialize the database"""
    active_engine = target_engine or engine
    Base.metadata.create_all(bind=active_engine)

    # Add potential_cost column for existing databases
    try:
        with active_engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE routing_logs ADD COLUMN potential_cost FLOAT")
            )
    except Exception:
        pass

    # Ensure time-window and failure-alert indexes exist (safe & idempotent on existing DBs)
    try:
        with active_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_routing_logs_timestamp "
                    "ON routing_logs (timestamp)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_routing_logs_success_timestamp "
                    "ON routing_logs (success, timestamp)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_routing_logs_model_timestamp "
                    "ON routing_logs (model_id, timestamp)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_routing_logs_agent_timestamp "
                    "ON routing_logs (agent_id, timestamp)"
                )
            )
    except Exception:
        pass
