import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.models.routing import RoutingRequest
from src.router.core import RoutingDecision


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_guardian_content_leak_protection(base_router):
    """
    Rule 1: Content Leak Check.
    If a model leaks raw tool tags (like '<function' or '✿') into the conversational text block,
    the Guardian must flag it as invalid and failover to the next candidate model.
    """
    request = RoutingRequest(
        query="Run python script",
        agent_id="test_agent",
        parameters={
            "messages": [{"role": "user", "content": "Run script"}],
            "tools": [{"type": "function", "function": {"name": "execute_code"}}]
        }
    )

    # First model (gemini-3.1-flash-lite) will leak hallucinated XML tags
    leaked_response = {
        "text": "Sure, I can run that script for you: <function name=\"execute_code\"></function>",
        "finish_reason": "stop"
    }
    
    # Second model (gemini-2.5-flash) responds perfectly
    clean_response = {
        "text": "Script executed successfully.",
        "finish_reason": "stop"
    }

    # Setup the mock adapters
    mock_leak_adapter = AsyncMock()
    mock_leak_adapter.forward_request.return_value = leaked_response

    mock_clean_adapter = AsyncMock()
    mock_clean_adapter.forward_request.return_value = clean_response

    base_router.adapters["gemini-3.1-flash-lite"] = mock_leak_adapter
    base_router.adapters["gemini-2.5-flash"] = mock_clean_adapter

    # Mock the /decide endpoint to return high success probabilities so both are tried
    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 401})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # We run tiered_assessment (Ladder) so it iterates over them
        response = await base_router.route_request(request, strategy="tiered_assessment")
        
        # Verify that the router successfully bypassed the leaking model and used the clean one
        assert response.model_id == "gemini-2.5-flash"
        assert response.response["text"] == "Script executed successfully."


@pytest.mark.asyncio
async def test_guardian_ghost_tool_prevention(base_router):
    """
    Rule 3: Ghost Tool Check.
    If the model attempts to invoke a tool that was NOT requested in the client's payload,
    the Guardian must reject the response as a ghost tool call and failover.
    """
    request = RoutingRequest(
        query="Write a poem",
        agent_id="test_agent",
        parameters={
            "messages": [{"role": "user", "content": "Write a poem"}],
            # NO tools requested!
        }
    )

    # First model (gemini-3.1-flash-lite) hallucinates a tool call to a random function
    ghost_response = {
        "text": "",
        "tool_calls": [
            {
                "id": "ghost_call_123",
                "type": "function",
                "function": {"name": "internet_search_api", "arguments": "{}"}
            }
        ],
        "finish_reason": "tool_calls"
    }
    
    # Second model responds with pure text
    clean_response = {
        "text": "Here is your beautiful poem...",
        "finish_reason": "stop"
    }

    mock_ghost_adapter = AsyncMock()
    mock_ghost_adapter.forward_request.return_value = ghost_response

    mock_clean_adapter = AsyncMock()
    mock_clean_adapter.forward_request.return_value = clean_response

    base_router.adapters["gemini-3.1-flash-lite"] = mock_ghost_adapter
    base_router.adapters["gemini-2.5-flash"] = mock_clean_adapter

    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 402})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = await base_router.route_request(request, strategy="tiered_assessment")
        
        # Verify that the router successfully rejected the ghost tool call and fell back
        assert response.model_id == "gemini-2.5-flash"
        assert response.response["text"] == "Here is your beautiful poem..."


@pytest.mark.asyncio
async def test_guardian_heuristic_tool_rescue(base_router):
    """
    Heuristic Tool Rescue Check.
    Some models dump JSON-formatted tool calls in their text content block instead of
    raising official tool_calls events. The Guardian must parse and "rescue" these
    into standard tool call structures.
    """
    request = RoutingRequest(
        query="Spawn a session",
        agent_id="test_agent",
        parameters={
            "messages": [{"role": "user", "content": "Spawn a session"}],
            "tools": [{"type": "function", "function": {"name": "sessions_spawn", "parameters": {}}}]
        }
    )

    # Model returns raw JSON buried inside markdown instead of invoking API tool calls
    buried_json_response = {
        "text": "Sure, I can spawn a session:\n```json\n{\n  \"name\": \"sessions_spawn\",\n  \"arguments\": {\"task\": \"qa\"}\n}\n```",
        "finish_reason": "stop"
    }

    mock_rescue_adapter = AsyncMock()
    mock_rescue_adapter.forward_request.return_value = buried_json_response
    base_router.adapters["gemini-3.1-flash-lite"] = mock_rescue_adapter

    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 403})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = await base_router.route_request(request, strategy="tiered_assessment")
        
        # Verify that the model's textual JSON was successfully rescued into a standard OpenAI tool_call
        assert response.model_id == "gemini-3.1-flash-lite"
        assert "tool_calls" in response.response
        tool_call = response.response["tool_calls"][0]
        assert tool_call["function"]["name"] == "sessions_spawn"
        assert json.loads(tool_call["function"]["arguments"]) == {"task": "qa"}
        # Confirm that the leaked text was cleared to avoid downstream parser loops
        assert response.response["text"] == ""


@pytest.mark.asyncio
async def test_guardian_action_block_rescue(base_router):
    """
    Action/Terminal Block Heuristic Rescue Check.
    Some models leak custom formatted tool tags (like '[Action: terminal({...})]') into the
    plain content block. The Guardian must parse, clean, and rescue these into valid tool_calls.
    """
    request = RoutingRequest(
        query="Run pytests in AIKYC directory",
        agent_id="test_agent",
        parameters={
            "messages": [{"role": "user", "content": "Run pytests"}],
            "tools": [{"type": "function", "function": {"name": "terminal", "parameters": {}}}]
        }
    )

    # Model returned a leaked Action block inside text (exactly like Zed/Aider leaks)
    leaked_action_response = {
        "text": "[Action: terminal({\"command\":\"export PYTHONPATH=$PYTHONPATH:.:cloud/deployment/backend && ./venv/bin/pytest cloud/tests/test_golden_dataset.py\",\"cd\":\"/home/lc/CodeProjects/AIKYC\"})]",
        "finish_reason": "stop"
    }

    mock_rescue_adapter = AsyncMock()
    mock_rescue_adapter.forward_request.return_value = leaked_action_response
    base_router.adapters["gemini-3.1-flash-lite"] = mock_rescue_adapter

    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 404})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = await base_router.route_request(request, strategy="tiered_assessment")
        
        # Verify that the leaked action block was successfully rescued
        assert response.model_id == "gemini-3.1-flash-lite"
        assert "tool_calls" in response.response
        tool_call = response.response["tool_calls"][0]
        assert tool_call["function"]["name"] == "terminal"
        
        # Verify arguments are parsed correctly as valid JSON
        args = json.loads(tool_call["function"]["arguments"])
        assert args["cd"] == "/home/lc/CodeProjects/AIKYC"
        assert "pytest cloud/tests/test_golden_dataset.py" in args["command"]
        
        # Confirm that the leaked text was cleared
        assert response.response["text"] == ""
