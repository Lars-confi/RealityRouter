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
