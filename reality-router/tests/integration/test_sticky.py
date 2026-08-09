import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.models.routing import RoutingRequest


@pytest.mark.asyncio
async def test_sticky_session_natural_lifecycle(base_router):
    """
    Verifies that when a multi-turn conversation begins:
    1. The first request is routed dynamically (ranked using Expected Utility).
    2. The selected model is automatically recorded as sticky in the active sessions.
    3. The second request in the same session is instantly routed to the sticky model,
       bypassing ranking/decider API calls.
    """
    from src.config.settings import get_settings
    get_settings().enable_sticky_sessions = True

    # 1. First turn request (contains only the initial user message)
    first_turn_payload = {
        "model": "RealRouter",
        "messages": [
            {"role": "user", "content": "Hello! I am starting a coding session."}
        ],
        "stream": False
    }
    
    first_request = RoutingRequest(
        query="Hello! I am starting a coding session.",
        agent_id="roo-code-agent-123",
        parameters=first_turn_payload
    )

    # First turn should evaluate and select the highest utility model.
    # We mock the /decide endpoint so that gemini-3.1-flash-lite is rated extremely high
    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        model_id = json.get("features", {}).get("model_id", "")
        prob = 0.99 if model_id == "gemini-3.1-flash-lite" else 0.1
        return MagicMock(status_code=200, json=lambda: {"prob_true": prob, "decision_id": 501})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post) as mock_post:
        # Route the first request (should select gemini-3.1-flash-lite)
        response_1 = await base_router.route_request(first_request, strategy="expected_utility")
        assert response_1.model_id == "gemini-3.1-flash-lite"
        assert mock_post.call_count == 3 # Polled all 3 models in the pool
        
        # Verify that the session is now registered in active sessions
        # The session ID format is: f"zed_{hash(agent_id + first_message)}"
        import hashlib
        import json as json_lib
        first_msg_str = json_lib.dumps(first_turn_payload["messages"][0], sort_keys=True)
        session_str = f"roo-code-agent-123_{first_msg_str}"
        session_hash = hashlib.sha256(session_str.encode("utf-8")).hexdigest()
        session_id = f"zed_{session_hash}"
        
        # CRITICAL TEST: It must have been registered automatically!
        assert session_id in base_router.active_sessions
        assert base_router.active_sessions[session_id] == "gemini-3.1-flash-lite"

    # 2. Second turn request (contains the first turn AND the user follow-up)
    second_turn_payload = {
        "model": "RealRouter",
        "messages": [
            {"role": "user", "content": "Hello! I am starting a coding session."},
            {"role": "assistant", "content": "Success Response"},
            {"role": "user", "content": "Now write a function to reverse a string."}
        ],
        "stream": False
    }
    
    second_request = RoutingRequest(
        query="Now write a function to reverse a string.",
        agent_id="roo-code-agent-123",
        parameters=second_turn_payload
    )

    # Route the second request. It MUST use the sticky session and bypass Snap API polling entirely!
    with patch("httpx.AsyncClient.post") as mock_post_2:
        response_2 = await base_router.route_request(second_request, strategy="expected_utility")
        
        # Verify it went straight to gemini-3.1-flash-lite
        assert response_2.model_id == "gemini-3.1-flash-lite"
        # Verify no decider API calls were made (bypassed)
        mock_post_2.assert_not_called()
