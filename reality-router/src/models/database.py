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
    reality_check_id = Column(String(100))  # Linked ID from Reality Check API
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

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize the database"""
    Base.metadata.create_all(bind=engine)

    # Add potential_cost column for existing databases
    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE routing_logs ADD COLUMN potential_cost FLOAT")
            )
    except Exception:
        pass
