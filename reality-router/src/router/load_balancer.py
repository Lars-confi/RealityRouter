"""
Load balancer for LLM routing system
"""

import logging
import time
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.database import ModelPerformance, RoutingLog, get_db
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class LoadBalancer:
    """Load balancer for distributing requests across LLM models"""

    def __init__(self):
        """Initialize the load balancer"""
        self.models = {}
        self.current_index = 0
        self.request_counts = {}
        self.success_counts = {}
        self.total_requests = 0
        self.circuit_breakers = {}  # Track circuit breaker states per model

        logger.info("Load balancer initialized")

    def add_model(self, model_id: str, model_name: str, weight: float = 1.0):
        """
        Add a model to the load balancer

        Args:
            model_id: Unique identifier for the model
            model_name: Name of the model
            weight: Weight for load distribution (higher = more requests)
        """
        self.models[model_id] = {
            "name": model_name,
            "weight": weight,
            "requests": 0,
            "successes": 0,
        }
        self.request_counts[model_id] = 0
        self.success_counts[model_id] = 0
        self.circuit_breakers[model_id] = {
            "state": "CLOSED",  # CLOSED, OPEN, HALF_OPEN
            "failure_count": 0,
            "last_failure_time": None,
            "last_attempt_time": None,
            "reset_timeout": 30.0,  # seconds before trying to reset
            "failure_threshold": 5,  # number of failures to trip circuit
        }
        logger.info(f"Added model {model_name} (ID: {model_id}) to load balancer")

    def get_next_model_round_robin(self) -> Optional[str]:
        """
        Get the next model using round-robin algorithm

        Returns:
            Model ID or None if no models available
        """
        if not self.models:
            return None

        model_ids = list(self.models.keys())
        model_id = model_ids[self.current_index]
        self.current_index = (self.current_index + 1) % len(model_ids)

        return model_id

    def get_next_model_weighted(self) -> Optional[str]:
        """
        Get the next model using weighted distribution

        Returns:
            Model ID or None if no models available
        """
        if not self.models:
            return None

        # Calculate total weight
        total_weight = sum(model["weight"] for model in self.models.values())

        if total_weight <= 0:
            # Fallback to round-robin if weights are invalid
            return self.get_next_model_round_robin()

        # Select model based on weights
        import random

        rand_value = random.uniform(0, total_weight)
        cumulative_weight = 0

        for model_id, model_info in self.models.items():
            cumulative_weight += model_info["weight"]
            if rand_value <= cumulative_weight:
                return model_id

        # Fallback to last model
        return list(self.models.keys())[-1]

    def get_next_model_performance_based(self, db: Session) -> Optional[str]:
        """
        Get the next model based on performance metrics

        Args:
            db: Database session

        Returns:
            Model ID or None if no models available
        """
        if not self.models:
            return None

        try:
            model_scores = {}
            for model_id, model_info in self.models.items():
                # Query recent performance metrics from database
                # Get the last 100 requests for this model to compute recent stats
                recent_logs = (
                    db.query(RoutingLog)
                    .filter(RoutingLog.model_id == model_id)
                    .order_by(RoutingLog.timestamp.desc())
                    .limit(100)
                    .all()
                )

                if not recent_logs:
                    # Fallback default values if no history exists
                    avg_time = 0.5
                    success_rate = 0.8
                else:
                    successes = sum(1 for log in recent_logs if log.success)
                    success_rate = successes / len(recent_logs)

                    times = [log.time for log in recent_logs if log.time is not None]
                    avg_time = sum(times) / len(times) if times else 0.5

                # Prevent division by zero and extremely small times
                avg_time = max(avg_time, 0.001)

                # Score calculation:
                # Better performance (lower time, higher success rate) yields higher score
                score = (1 / avg_time) * success_rate * model_info["weight"]
                model_scores[model_id] = score

            # Select model with highest score
            best_model = max(model_scores, key=model_scores.get)
            return best_model

        except Exception as e:
            logger.error(
                f"Error calculating performance-based model selection: {str(e)}"
            )
            # Fallback to weighted distribution
            return self.get_next_model_weighted()

    def get_next_model(
        self, strategy: str = "round_robin", db: Session = None
    ) -> Optional[str]:
        """
        Get the next model based on the specified strategy

        Args:
            strategy: Selection strategy ("round_robin", "weighted", "performance_based")
            db: Database session (required for performance_based strategy)

        Returns:
            Model ID or None if no models available
        """
        if strategy == "round_robin":
            return self.get_next_model_round_robin()
        elif strategy == "weighted":
            return self.get_next_model_weighted()
        elif strategy == "performance_based":
            if db is None:
                logger.warning("Performance-based strategy requires database session")
                return self.get_next_model_weighted()
            return self.get_next_model_performance_based(db)
        else:
            logger.warning(f"Unknown strategy {strategy}, using round-robin")
            return self.get_next_model_round_robin()

    def is_model_healthy(self, model_id: str) -> bool:
        """
        Check if a model is healthy (not circuit-tripped)

        Args:
            model_id: Model identifier

        Returns:
            True if model is healthy, False if circuit is open
        """
        if model_id not in self.circuit_breakers:
            return True  # No circuit breaker, assume healthy

        cb = self.circuit_breakers[model_id]

        if cb["state"] == "CLOSED":
            return True
        elif cb["state"] == "OPEN":
            # Check if timeout has passed to attempt reset
            if cb["last_failure_time"] is not None:
                elapsed = time.time() - cb["last_failure_time"]
                if elapsed >= cb["reset_timeout"]:
                    cb["state"] = "HALF_OPEN"
                    logger.info(
                        f"Model {model_id} circuit breaker half-opened after {cb['reset_timeout']}s"
                    )
                return False
            return False
        elif cb["state"] == "HALF_OPEN":
            # In half-open state, allow one request to test
            return True
        return True

    def record_failure(self, model_id: str):
        """
        Record a failure for a model and update circuit breaker state

        Args:
            model_id: Model identifier
        """
        if model_id not in self.circuit_breakers:
            return

        cb = self.circuit_breakers[model_id]
        cb["last_failure_time"] = time.time()
        cb["failure_count"] += 1
        cb["last_attempt_time"] = time.time()

        if cb["failure_count"] >= cb["failure_threshold"]:
            cb["state"] = "OPEN"
            logger.warning(
                f"Model {model_id} circuit breaker OPEN after {cb['failure_count']} failures"
            )

    def record_success(self, model_id: str):
        """
        Record a success for a model and reset circuit breaker

        Args:
            model_id: Model identifier
        """
        if model_id not in self.circuit_breakers:
            return

        cb = self.circuit_breakers[model_id]
        cb["last_attempt_time"] = time.time()

        if cb["state"] == "OPEN":
            # Reset circuit breaker after successful test
            cb["state"] = "CLOSED"
            cb["failure_count"] = 0
            logger.info(f"Model {model_id} circuit breaker reset to CLOSED")

    def update_metrics(self, model_id: str, success: bool = True):
        """
        Update metrics for a model

        Args:
            model_id: Model identifier
            success: Whether the request was successful
        """
        if model_id in self.models:
            self.models[model_id]["requests"] += 1
            self.request_counts[model_id] += 1
            self.total_requests += 1

            if success:
                self.models[model_id]["successes"] += 1
                self.success_counts[model_id] += 1

    def get_model_stats(self) -> Dict:
        """
        Get statistics for all models

        Returns:
            Dictionary with model statistics
        """
        stats = {}
        for model_id, model_info in self.models.items():
            total_requests = self.request_counts.get(model_id, 0)
            successes = self.success_counts.get(model_id, 0)
            success_rate = successes / total_requests if total_requests > 0 else 0.0

            stats[model_id] = {
                "name": model_info["name"],
                "requests": total_requests,
                "successes": successes,
                "success_rate": success_rate,
                "weight": model_info["weight"],
            }

        return stats


# Global load balancer instance
load_balancer = LoadBalancer()
