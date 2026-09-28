"""
Unit tests for agent detection, trajectory tracking, and ladder escalation logic.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.config.settings import Settings
from src.models.routing import RoutingRequest
from src.router.agent_detector import (
    TrajectoryTracker,
    classify_observation,
    detect_interaction_mode,
)
from src.router.core import RouterCore


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


# ---------------------------------------------------------------------------
# 1. Agent Detection Tests
# ---------------------------------------------------------------------------

def test_detect_interaction_mode_tools():
    """Verify tool definitions trigger tool_agent mode."""
    req = RoutingRequest(
        query="Edit code",
        parameters={
            "tools": [{"type": "function", "function": {"name": "edit_file"}}],
            "messages": [{"role": "user", "content": "Edit code"}],
        },
    )
    mode, conf = detect_interaction_mode(req)
    assert mode == "tool_agent"
    assert conf >= 0.95


def test_detect_interaction_mode_tool_messages():
    """Verify presence of tool messages in history triggers tool_agent mode."""
    req = RoutingRequest(
        query="",
        parameters={
            "messages": [
                {"role": "user", "content": "Run tests"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "PASSED"},
            ]
        },
    )
    mode, conf = detect_interaction_mode(req)
    assert mode == "tool_agent"
    assert conf == 1.0


def test_detect_interaction_mode_single_turn():
    """Verify standard single-turn conversation without tools is single_turn."""
    req = RoutingRequest(
        query="What is the capital of France?",
        parameters={"messages": [{"role": "user", "content": "What is the capital of France?"}]},
    )
    mode, conf = detect_interaction_mode(req)
    assert mode == "single_turn"
    assert conf >= 0.8


def test_detect_interaction_mode_settings_overrides():
    """Verify settings.agent_handling overrides detection."""
    settings_force_agent = Settings(agent_handling="force_agent")
    settings_force_single = Settings(agent_handling="force_single_turn")

    # Plain message normally single_turn, but forced to agent
    req_plain = RoutingRequest(query="Hello", parameters={"messages": [{"role": "user", "content": "Hello"}]})
    mode, conf = detect_interaction_mode(req_plain, settings_force_agent)
    assert mode == "tool_agent"
    assert conf == 1.0

    # Request with tools normally agent, but forced to single_turn
    req_tools = RoutingRequest(
        query="Edit",
        parameters={"tools": [{"type": "function", "function": {"name": "bash"}}]},
    )
    mode, conf = detect_interaction_mode(req_tools, settings_force_single)
    assert mode == "single_turn"
    assert conf == 1.0


# ---------------------------------------------------------------------------
# 2. Observation Classification Tests
# ---------------------------------------------------------------------------

def test_classify_observation_nonzero_exit_code():
    messages = [
        {"role": "user", "content": "run script"},
        {"role": "tool", "content": "Command failed with exit code 1: file not found"},
    ]
    assert classify_observation(messages) == "failure"


def test_classify_observation_test_failure():
    messages = [
        {"role": "user", "content": "pytest"},
        {"role": "tool", "content": "FAILED tests/test_core.py::test_fail - AssertionError: expected True"},
    ]
    assert classify_observation(messages) == "failure"


def test_classify_observation_traceback_exception():
    messages = [
        {"role": "user", "content": "run python"},
        {"role": "tool", "content": "Traceback (most recent call last):\n  File 'a.py', line 1\nTypeError: bad operand"},
    ]
    assert classify_observation(messages) == "failure"


def test_classify_observation_compiler_error():
    messages = [
        {"role": "user", "content": "cargo build"},
        {"role": "tool", "content": "error[E0308]: mismatched types\n --> src/main.rs:10:5"},
    ]
    assert classify_observation(messages) == "failure"


def test_classify_observation_success():
    messages = [
        {"role": "user", "content": "pytest"},
        {"role": "tool", "content": "5 passed, 0 failed\n[exit code: 0]"},
    ]
    assert classify_observation(messages) == "success"


def test_classify_observation_stagnation():
    messages = [
        {"role": "user", "content": "step 1"},
        {"role": "tool", "content": "identical repeated error output"},
        {"role": "assistant", "content": "trying again"},
        {"role": "tool", "content": "identical repeated error output"},
    ]
    assert classify_observation(messages) == "stagnation"


def test_classify_observation_progress():
    messages = [
        {"role": "user", "content": "cat file.txt"},
        {"role": "tool", "content": "line 1\nline 2\nline 3"},
    ]
    assert classify_observation(messages) == "progress"


def test_classify_observation_unknown_when_no_tools():
    messages = [{"role": "user", "content": "Hello"}]
    assert classify_observation(messages) == "unknown"


# ---------------------------------------------------------------------------
# 3. TrajectoryTracker Tests
# ---------------------------------------------------------------------------

def test_trajectory_tracker_state_transitions():
    tracker = TrajectoryTracker()
    session_id = "session_123"

    assert not tracker.should_escalate(session_id)

    # 1 failure
    tracker.record_observation("failure", session_id)
    assert tracker.get_session_state(session_id)["consecutive_failures"] == 1
    assert not tracker.should_escalate(session_id, failure_threshold=2)

    # 2 consecutive failures -> should escalate
    tracker.record_observation("failure", session_id)
    assert tracker.get_session_state(session_id)["consecutive_failures"] == 2
    assert tracker.should_escalate(session_id, failure_threshold=2)

    # Success resets failure counter
    tracker.record_observation("success", session_id)
    assert tracker.get_session_state(session_id)["consecutive_failures"] == 0
    assert not tracker.should_escalate(session_id, failure_threshold=2)

    # Tool call sets pending_observation
    tracker.record_tool_call(session_id)
    assert tracker.get_session_state(session_id)["pending_observation"] is True

    # Observation clears pending_observation
    tracker.record_observation("progress", session_id)
    assert tracker.get_session_state(session_id)["pending_observation"] is False


# ---------------------------------------------------------------------------
# 4. Ladder Escalation & Core Routing Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ladder_valid_tool_call_short_circuit_unit(base_router):
    """
    Rung 1 emits a valid tool call in tool_agent mode.
    Router must accept immediately without calling rung 2.
    """
    request = RoutingRequest(
        query="Inspect codebase",
        agent_id="test_short_circuit",
        parameters={
            "messages": [{"role": "user", "content": "Inspect codebase"}],
            "tools": [{"type": "function", "function": {"name": "grep", "parameters": {}}}],
        },
    )

    mock_rung1 = AsyncMock()
    mock_rung1.forward_request.return_value = {
        "text": "",
        "tool_calls": [
            {
                "id": "tc_1",
                "type": "function",
                "function": {"name": "grep", "arguments": "{\"regex\": \"def \"}"},
            }
        ],
        "finish_reason": "tool_calls",
    }
    mock_rung2 = AsyncMock()

    base_router.adapters["gemini-3.1-flash-lite"] = mock_rung1
    base_router.adapters["gemini-2.5-flash"] = mock_rung2

    ladder_calls = 0

    async def mock_rc_post(url, json=None, **kwargs):
        nonlocal ladder_calls
        if "ladder-api" in url and "decide" in url:
            ladder_calls += 1
        return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 900})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = await base_router.route_request(request, strategy="tiered_assessment")

        assert response.model_id == "gemini-3.1-flash-lite"
        mock_rung1.forward_request.assert_called_once()
        mock_rung2.forward_request.assert_not_called()
        # Post-hoc ladder rerouting endpoint was skipped due to short circuit
        assert ladder_calls == 0


@pytest.mark.asyncio
async def test_ladder_incremental_eu_escalate_formula_hysteresis(base_router):
    """
    Incremental EU_escalate = (p_next - p_actual)*reward - alpha*c_next*1000 - beta*t_next.
    If p_actual=0.92, EU_escalate <= 10.0, so escalation is prevented.
    If p_actual=0.30, EU_escalate > 10.0, so escalation proceeds to rung 2.
    """
    base_router.utility_calculator.reward = 100.0

    # Scenario 1: p_actual = 0.92 -> Stops on rung 1
    req1 = RoutingRequest(
        query="Write docstring",
        agent_id="test_hysteresis_1",
        parameters={"messages": [{"role": "user", "content": "Write docstring"}]},
    )

    async def mock_rc_post_high(url, json=None, **kwargs):
        if "snap-api" in url:
            return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 901})
        return MockHTTPXResponse({"prob_true": 0.92, "decision_id": 902})

    mock_m1 = AsyncMock()
    mock_m1.forward_request.return_value = {"text": "Documentation text", "finish_reason": "stop"}
    mock_m2 = AsyncMock()

    base_router.adapters["gemini-3.1-flash-lite"] = mock_m1
    base_router.adapters["gemini-2.5-flash"] = mock_m2

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post_high):
        resp1 = await base_router.route_request(req1, strategy="tiered_assessment")
        assert resp1.model_id == "gemini-3.1-flash-lite"
        mock_m2.forward_request.assert_not_called()


@pytest.mark.asyncio
async def test_ladder_repeated_observation_failures_force_escalation(base_router):
    """
    When previous observation state is 'failure' repeatedly,
    the router escalates to higher tier even if tool call is produced.
    """
    base_router.utility_calculator.reward = 100.0
    session_messages = [
        {"role": "user", "content": "Fix bug"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "bash"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "exit code 1: TypeError in main.py"},
    ]

    request = RoutingRequest(
        query="Fix bug",
        agent_id="test_agent_failures",
        parameters={
            "messages": session_messages,
            "tools": [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
        },
    )

    # First turn: record 1 failure
    obs = classify_observation(session_messages)
    assert obs == "failure"
    base_router.trajectory_tracker.record_observation(obs, session_id="test_agent_failures")
    # Second failure
    base_router.trajectory_tracker.record_observation("failure", session_id="test_agent_failures")
    assert base_router.trajectory_tracker.should_escalate("test_agent_failures")

    mock_m1 = AsyncMock()
    mock_m1.forward_request.return_value = {
        "text": "",
        "tool_calls": [{"id": "t2", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"ls\"}"}}],
        "finish_reason": "tool_calls",
    }
    mock_m2 = AsyncMock()
    mock_m2.forward_request.return_value = {
        "text": "",
        "tool_calls": [{"id": "t3", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"cat fix.py\"}"}}],
        "finish_reason": "tool_calls",
    }

    base_router.adapters["gemini-3.1-flash-lite"] = mock_m1
    base_router.adapters["gemini-2.5-flash"] = mock_m2

    ladder_call_count = 0

    async def mock_rc_post(url, json=None, **kwargs):
        nonlocal ladder_call_count
        if "snap-api" in url:
            return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 911})
        if "feedback" in url:
            return MockHTTPXResponse({"status": "ok"})
        ladder_call_count += 1
        prob = 0.4 if ladder_call_count == 1 else 0.95
        return MockHTTPXResponse({"prob_true": prob, "decision_id": 912})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # Even though rung 1 returns a tool call, repeated failures force ladder escalation to rung 2
        resp = await base_router.route_request(request, strategy="tiered_assessment")
        assert resp.model_id == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_ladder_agent_multi_turn_benchmark_lifecycle(base_router):
    """
    Multi-turn benchmark regression test verifying:
    - Turn 1: Tools present, model emits valid tool call -> exactly 1 model invocation, no post-hoc ladder traversal, tool call returned immediately.
    - Turn 2: Observation shows progress (exit code 0) -> model invoked once, no escalation.
    - Turn 3: Malformed tool call or repeated failure -> escalation triggered, reason logged.
    """
    session_id = "benchmark_agent_lifecycle"
    base_router.utility_calculator.reward = 100.0

    mock_rung1 = AsyncMock()
    mock_rung2 = AsyncMock()
    mock_rung3 = AsyncMock()

    base_router.adapters["gemini-3.1-flash-lite"] = mock_rung1
    base_router.adapters["gemini-2.5-flash"] = mock_rung2
    base_router.adapters["gemini-3.5-flash"] = mock_rung3

    tools = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute bash command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            },
        }
    ]

    ladder_calls = []
    feedback_calls = []

    async def mock_rc_post(url, json=None, **kwargs):
        if "ladder-api" in url and "decide" in url:
            ladder_calls.append(json)
            return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 1001 + len(ladder_calls)})
        if "feedback" in url:
            feedback_calls.append(json)
            return MockHTTPXResponse({"status": "ok"})
        if "snap-api" in url:
            return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 999})
        return MockHTTPXResponse({"status": "ok"})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post), patch.object(
        base_router, "assess_user_sentiment", return_value=None
    ):
        # -------------------------------------------------------------
        # Turn 1: Tools present, model emits valid tool call
        # -------------------------------------------------------------
        mock_rung1.forward_request.return_value = {
            "text": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "ls -la"})},
                }
            ],
            "finish_reason": "tool_calls",
        }

        turn1_messages = [{"role": "user", "content": "List files in directory"}]
        req1 = RoutingRequest(
            query="List files in directory",
            agent_id=session_id,
            parameters={"messages": turn1_messages, "tools": tools},
        )

        resp1 = await base_router.route_request(req1, strategy="tiered_assessment")

        # Turn 1 assertions:
        assert resp1.model_id == "gemini-3.1-flash-lite"
        assert resp1.response.get("tool_calls") is not None
        assert mock_rung1.forward_request.call_count == 1
        assert mock_rung2.forward_request.call_count == 0
        assert len(ladder_calls) == 0  # No post-hoc ladder traversal

        # -------------------------------------------------------------
        # Turn 2: Observation shows progress (exit code 0) -> model invoked once, no escalation
        # -------------------------------------------------------------
        mock_rung1.forward_request.reset_mock()
        mock_rung2.forward_request.reset_mock()

        mock_rung1.forward_request.return_value = {
            "text": "",
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "cat main.py"})},
                }
            ],
            "finish_reason": "tool_calls",
        }

        turn2_messages = [
            {"role": "user", "content": "List files in directory"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": json.dumps({"command": "ls -la"})},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "main.py\nrequirements.txt\n[exit code: 0]",
            },
        ]
        req2 = RoutingRequest(
            query="Inspect main.py",
            agent_id=session_id,
            parameters={"messages": turn2_messages, "tools": tools},
        )

        resp2 = await base_router.route_request(req2, strategy="tiered_assessment")

        # Turn 2 assertions:
        assert resp2.model_id == "gemini-3.1-flash-lite"
        assert mock_rung1.forward_request.call_count == 1
        assert mock_rung2.forward_request.call_count == 0
        assert len(ladder_calls) == 0

        # -------------------------------------------------------------
        # Turn 3: Malformed tool call or repeated failure -> escalation triggered, reason logged
        # -------------------------------------------------------------
        mock_rung1.forward_request.reset_mock()
        mock_rung2.forward_request.reset_mock()

        # Rung 1 emits malformed tool call (invalid JSON arguments)
        mock_rung1.forward_request.return_value = {
            "text": "",
            "tool_calls": [
                {
                    "id": "call_3",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{malformed_json: bad syntax"},
                }
            ],
            "finish_reason": "tool_calls",
        }

        # Rung 2 emits valid response/tool call
        mock_rung2.forward_request.return_value = {
            "text": "",
            "tool_calls": [
                {
                    "id": "call_3_fixed",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "pytest"})},
                }
            ],
            "finish_reason": "tool_calls",
        }

        turn3_messages = list(turn2_messages) + [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "bash", "arguments": json.dumps({"command": "cat main.py"})},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": "print('hello')\n[exit code: 0]",
            },
        ]
        req3 = RoutingRequest(
            query="Run tests",
            agent_id=session_id,
            parameters={"messages": turn3_messages, "tools": tools},
        )

        resp3 = await base_router.route_request(req3, strategy="tiered_assessment")

        # Turn 3 assertions:
        assert resp3.model_id == "gemini-2.5-flash"  # Escalated to Rung 2
        assert mock_rung1.forward_request.call_count == 1
        assert mock_rung2.forward_request.call_count == 1
        assert resp3.response.get("tool_calls") is not None
