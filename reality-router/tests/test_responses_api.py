import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from src.main import app
from src.models.routing import RoutingRequest
from src.router.core import router_core

client = TestClient(app)

class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def mock_router_dependencies():
    """Mock model capability probes, active models, and calibration endpoint calls."""
    # Mock active models in the pool
    router_core.models = {
        "gemini-3.8-flash": {
            "name": "Gemini 3.8 Flash",
            "cost": 0.001,
            "time": 1.0,
            "probability": 0.8,
            "supports_function_calling": True,
        }
    }
    mock_adapter = AsyncMock()
    mock_adapter.forward_request.return_value = {
        "text": "Success Response",
        "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25}
    }
    router_core.adapters = {
        "gemini-3.8-flash": mock_adapter
    }
    router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)


def test_responses_plain_string_input():
    """1. input = plain string"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": "Reply with exactly RR_OK",
        "max_output_tokens": 16
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "gemini-3.8-flash"


def test_responses_user_message_content_array():
    """2. user message with content[]"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Hello world"
                    }
                ]
            }
        ],
        "max_output_tokens": 16
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200


def test_responses_assistant_message_with_output_text():
    """3. assistant message with output_text content[]"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": [
            {
                "role": "user",
                "content": "Check files"
            },
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "I will inspect the repository."
                    }
                ]
            }
        ],
        "max_output_tokens": 16
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200


def test_responses_function_call_and_output():
    """4. function_call + function_call_output"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": [
            {
                "role": "user",
                "content": "Run git status"
            },
            {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_test",
                "name": "bash",
                "arguments": "{\"command\":\"git status\"}",
                "status": "completed"
            },
            {
                "type": "function_call_output",
                "call_id": "call_test",
                "output": "nothing to commit"
            }
        ],
        "max_output_tokens": 16
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200


def test_responses_assistant_content_array_with_tool_call_and_output():
    """5. assistant content[] + function_call + function_call_output"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Inspect the repository"
                    }
                ]
            },
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "I will inspect the repository."
                    }
                ]
            },
            {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_test",
                "name": "bash",
                "arguments": "{\"command\":\"git status\"}",
                "status": "completed"
            },
            {
                "type": "function_call_output",
                "call_id": "call_test",
                "output": "nothing to commit"
            }
        ],
        "max_output_tokens": 16
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200


def test_responses_two_consecutive_tool_call_turns():
    """6. two consecutive tool-call turns"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": [
            {
                "role": "user",
                "content": "Do things"
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "bash",
                "arguments": "{}",
                "status": "completed"
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "first output"
            },
            {
                "type": "function_call",
                "id": "fc_2",
                "call_id": "call_2",
                "name": "bash",
                "arguments": "{}",
                "status": "completed"
            },
            {
                "type": "function_call_output",
                "call_id": "call_2",
                "output": "second output"
            }
        ],
        "max_output_tokens": 16
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200


def test_responses_exact_reproduction_payload():
    """7. Exact reproduction payload provided in the bug description"""
    payload = {
        "model": "gemini-3.8-flash",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Inspect the git repository and continue working."
                    }
                ]
            },
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "I will inspect the git status, current branch, and directory contents of the repository.",
                        "annotations": []
                    }
                ]
            },
            {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_test",
                "name": "bash",
                "arguments": "{\"command\":\"git status && git branch\"}",
                "status": "completed"
            },
            {
                "type": "function_call_output",
                "call_id": "call_test",
                "output": "On branch master\nnothing to commit, working tree clean\n* master"
            }
        ],
        "tools": [
            {
                "type": "function",
                "name": "bash",
                "description": "Execute a bash command",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string"
                        }
                    },
                    "required": ["command"]
                }
            }
        ],
        "max_output_tokens": 512
    }
    
    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"


def _parse_sse_events(raw_text: str):
    events = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        ev_name, data_line = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev_name = line[7:].strip()
            elif line.startswith("data: "):
                data_line = line[6:].strip()
        assert ev_name is not None and data_line is not None
        events.append((ev_name, json.loads(data_line)))
    return events


def test_responses_streaming_text_contract():
    """8. stream=True text response emits ordered Responses SSE lifecycle."""
    router_core.adapters["gemini-3.8-flash"].forward_request.return_value = {
        "text": "Hello streamed response",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json={"model": "gemini-3.8-flash", "input": "Hi", "stream": True},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(response.text)
    names = [name for name, _ in events]
    seqs = [body["sequence_number"] for _, body in events]
    assert names == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert seqs == list(range(len(events)))
    final_resp = events[-1][1]["response"]
    assert final_resp["status"] == "completed"
    assert final_resp["model"] == "gemini-3.8-flash"
    assert final_resp["output"][0]["content"][0]["text"] == "Hello streamed response"


def test_responses_streaming_tool_call_only_contract():
    """9. stream=True tool-call-only reply strips empty message and streams arguments."""
    router_core.adapters["gemini-3.8-flash"].forward_request.return_value = {
        "text": "",
        "finish_reason": "tool_calls",
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Stockholm"}'},
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }

    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    payload = {
        "model": "gemini-3.8-flash",
        "input": "Weather in Stockholm?",
        "stream": True,
        "tools": [
            {
                "type": "function",
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
    }
    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json=payload,
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(response.text)
    names = [name for name, _ in events]
    assert names == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert [body["sequence_number"] for _, body in events] == list(range(len(events)))
    added_item = events[2][1]["item"]
    assert added_item["type"] == "function_call"
    assert added_item["status"] == "in_progress"
    assert added_item["arguments"] == ""
    assert events[3][1]["delta"] == '{"city":"Stockholm"}'
    assert events[4][1]["arguments"] == '{"city":"Stockholm"}'
    final_output = events[-1][1]["response"]["output"]
    assert len(final_output) == 1
    assert final_output[0]["type"] == "function_call"
    assert final_output[0]["name"] == "get_weather"
    assert final_output[0]["arguments"] == '{"city":"Stockholm"}'


def test_responses_streaming_error_handling():
    """10. stream=True routing exception returns HTTP 500 JSON detail."""
    with patch.object(router_core, "route_request", side_effect=RuntimeError("upstream exploded")):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer rr-local"},
            json={"model": "gemini-3.8-flash", "input": "Hi", "stream": True},
        )
    assert response.status_code == 500
    assert "upstream exploded" in response.json()["detail"]
