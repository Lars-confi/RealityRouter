import time
import pytest
from unittest.mock import MagicMock
from src.router.load_balancer import LoadBalancer


@pytest.fixture
def lb():
    """Provides a fresh LoadBalancer instance with two test models."""
    balancer = LoadBalancer()
    balancer.add_model("model-a", "Model A", weight=1.0)
    balancer.add_model("model-b", "Model B", weight=3.0) # Model B is weighted 3x heavier
    return balancer


def test_load_balancer_round_robin(lb):
    """
    Verifies that the round-robin selection sequentially cycles through available models.
    """
    # First select Model A, then Model B, then loop back to Model A
    assert lb.get_next_model("round_robin") == "model-a"
    assert lb.get_next_model("round_robin") == "model-b"
    assert lb.get_next_model("round_robin") == "model-a"


def test_load_balancer_weighted_distribution(lb):
    """
    Verifies that weighted selection respects model weights over a large sample size.
    """
    selections = []
    for _ in range(1000):
        selections.append(lb.get_next_model("weighted"))
        
    count_a = selections.count("model-a")
    count_b = selections.count("model-b")
    
    # Model B weight is 3.0, Model A weight is 1.0. 
    # Over 1000 trials, Model B should be chosen roughly 75% of the time (tolerance ± 10%)
    ratio_b = count_b / len(selections)
    assert 0.65 <= ratio_b <= 0.85, f"Expected B ratio around 0.75, got {ratio_b:.2f}"


def test_circuit_breaker_tripping_flow(lb):
    """
    Tests the complete lifecycle of a circuit breaker:
    CLOSED -> (failures) -> OPEN (unhealthy) -> (timeout) -> HALF-OPEN -> CLOSED (reset)
    """
    model = "model-a"
    cb = lb.circuit_breakers[model]
    
    # Initial state is CLOSED
    assert lb.is_model_healthy(model) is True
    assert cb["state"] == "CLOSED"
    
    # Fail 4 times (Threshold is 5)
    for _ in range(4):
        lb.record_failure(model)
    assert lb.is_model_healthy(model) is True # Still healthy
    assert cb["state"] == "CLOSED"
    
    # 5th failure trips the circuit
    lb.record_failure(model)
    assert lb.is_model_healthy(model) is False # Tripped! No longer healthy
    assert cb["state"] == "OPEN"
    
    # Simulate waiting past the reset timeout (30 seconds default)
    # We patch time.time() to fast-forward 35 seconds
    future_time = time.time() + 35.0
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(time, "time", lambda: future_time)
        
        # When checking health, it should transition to HALF_OPEN
        assert lb.is_model_healthy(model) is False # Still returns False (blocks requests) but changes state
        assert cb["state"] == "HALF_OPEN"
        
        # A HALF_OPEN circuit accepts requests. If it succeeds, it resets to CLOSED.
        lb.record_success(model)
        assert lb.is_model_healthy(model) is True
        assert cb["state"] == "CLOSED"
        assert cb["failure_count"] == 0 # Reset!
