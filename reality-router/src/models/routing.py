"""
Data models for routing requests and responses
"""

import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class RoutingRequest(BaseModel):
    """Request model for routing"""

    query: str
    agent_id: Optional[str] = "default"
    parameters: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime.datetime] = None

    class Config:
        json_schema_extra = {
            "example": {
                "query": "Explain quantum computing in simple terms",
                "parameters": {"max_tokens": 100, "temperature": 0.7},
            }
        }


class RoutingResponse(BaseModel):
    """Response model for routing"""

    agent_id: Optional[str] = "default"
    model_id: str
    model_name: str
    expected_utility: float
    cost: float
    time: float
    probability: float
    response: Dict[str, Any]
    timestamp: Optional[datetime.datetime] = None

    class Config:
        json_schema_extra = {
            "example": {
                "model_id": "openai_gpt-3.5-turbo",
                "model_name": "GPT-3.5 Turbo",
                "expected_utility": 0.85,
                "cost": 0.002,
                "time": 0.5,
                "probability": 0.9,
                "response": {
                    "text": "Quantum computing uses quantum bits (qubits) that can exist in multiple states simultaneously...",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 50,
                        "total_tokens": 60,
                    },
                },
            }
        }
