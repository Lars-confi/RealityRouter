"""
Unit tests for LiteLLM adapter.
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from src.adapters.litellm_adapter import extract_message_text, LiteLLMAdapter
from src.models.routing import RoutingRequest

# ---------------------------------------------------------------------------
# extract_message_text tests
# ---------------------------------------------------------------------------
def test_extract_message_text_string():
    assert extract_message_text("  Sample text  ") == "Sample text"

def test_extract_message_text_list():
    assert extract_message_text(["line 1", "  line2  "]) == "line 1\n  line2"

def test_extract_message_text_mixed():
    mixed = ["pre", {"type": "text", "text": "middle"}, {"ignore": "data"}, {"type": "text", "text": "post"}]
    assert extract_message_text(mixed) == "pre\nmiddle\npost"

def test_extract_message_text_empty():
    assert extract_message_text(None) == ""
    assert extract_message_text([]) == ""

# ---------------------------------------------------------------------------
# LiteLLMAdapter.forward_request tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forward_request_invalid_input():
    adapter = LiteLLMAdapter(model_name="test")
    # Case 1: both empty -> ValueError
    req1 = RoutingRequest(query="", parameters={})
    with pytest.raises(ValueError, match="Invalid request format"):
        await adapter.forward_request(req1)
    
    # Case 2: query empty but messages present -> Success
    messages = [{"role": "user", "content": "hi"}]
    req2 = RoutingRequest(query="", parameters={"messages": messages})
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="ok", role="assistant"), finish_reason="stop")])):
        res = await adapter.forward_request(req2)
        assert res["text"] == "ok"

@pytest.mark.asyncio
async def test_forward_request_basic_params():
    adapter = LiteLLMAdapter(model_name="test", api_key="key", base_url="http://base")
    req = RoutingRequest(query="hi", parameters={"temperature": 0.7, "stream": True})
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="ok", role="assistant"), finish_reason="stop")])) as mock_call:
        await adapter.forward_request(req)
        args = mock_call.call_args.kwargs
        assert args["temperature"] == 0.7
        assert args["api_key"] == "key"
        assert args["base_url"] == "http://base"
        assert args["stream"] is False  # Forced to False

@pytest.mark.asyncio
async def test_forward_request_gemini_handling():
    adapter = LiteLLMAdapter(model_name="gemini-pro")
    # Assistant tool calls and tool role in messages
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "thinking", "tool_calls": [{"function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "name": "t", "content": "result"}
    ]
    req = RoutingRequest(query="hi", parameters={"messages": messages, "frequency_penalty": 0.1})
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="ok", role="assistant"), finish_reason="stop")])) as mock_call:
        await adapter.forward_request(req)
        args = mock_call.call_args.kwargs
        
        # Penalty params stripped
        assert "frequency_penalty" not in args
        # Messages modified (flattened)
        modified_msgs = args["messages"]
        assert modified_msgs[1]["role"] == "assistant"
        assert "tool_calls" not in modified_msgs[1]
        assert "[Action: t({})]" in modified_msgs[1]["content"]
        assert modified_msgs[2]["role"] == "user"
        assert "[Action Result for t]:" in modified_msgs[2]["content"]
        
        # Verify original request was NOT mutated
        assert "tool_calls" in messages[1]

@pytest.mark.asyncio
async def test_forward_request_reasoning_extraction():
    adapter = LiteLLMAdapter(model_name="test")
    req = RoutingRequest(query="hi", parameters={})
    
    # Case 1: Direct reasoning_content
    mock_msg_1 = SimpleNamespace(
        content="", 
        role="assistant", 
        reasoning_content="thought_process",
        provider_specific_fields={}
    )
    mock_resp_1 = MagicMock(choices=[MagicMock(message=mock_msg_1, finish_reason="stop")])
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=mock_resp_1):
        res = await adapter.forward_request(req)
        assert res["text"] == "thought_process"
        assert res["reasoning_content"] == "thought_process"

    # Case 2: provider_specific_fields fallback
    mock_msg_2 = SimpleNamespace(
        content="", 
        role="assistant", 
        reasoning_content="",
        provider_specific_fields={"thought": "deep_thought"}
    )
    mock_resp_2 = MagicMock(choices=[MagicMock(message=mock_msg_2, finish_reason="stop")])
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=mock_resp_2):
        res = await adapter.forward_request(req)
        assert res["text"] == "deep_thought"
        assert res["reasoning_content"] == "deep_thought"

@pytest.mark.asyncio
async def test_forward_request_normalization():
    adapter = LiteLLMAdapter(model_name="test")
    req = RoutingRequest(query="hi", parameters={})
    
    # Mock complex response
    mock_msg = SimpleNamespace(
        content="ok", 
        role="assistant", 
        tool_calls=[
            SimpleNamespace(
                id="call-1", 
                type="function", 
                function=SimpleNamespace(name="t", arguments="{}")
            )
        ]
    )
    
    mock_usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    
    # Real 2-token logprobs sequence
    # Token 1: -0.1, top_logprobs: [-0.1, -0.2]
    # Token 2: -0.2, top_logprobs: [-0.1, -0.2]
    logprobs_content = [
        SimpleNamespace(
            logprob=-0.1, 
            top_logprobs=[SimpleNamespace(logprob=-0.1), SimpleNamespace(logprob=-0.2)]
        ),
        SimpleNamespace(
            logprob=-0.2, 
            top_logprobs=[SimpleNamespace(logprob=-0.1), SimpleNamespace(logprob=-0.2)]
        )
    ]
    
    mock_logprobs = SimpleNamespace(content=logprobs_content)
    
    mock_resp = MagicMock(
        choices=[MagicMock(message=mock_msg, finish_reason="stop", logprobs=mock_logprobs)],
        usage=mock_usage
    )
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=mock_resp):
        res = await adapter.forward_request(req)
        assert res["usage"]["prompt_tokens"] == 10
        assert len(res["tool_calls"]) == 1
        assert res["tool_calls"][0]["function"]["name"] == "t"
        
        # Metrics checks: top-level keys (not under 'logprobs')
        assert res["first_token_logprob"] == -0.1
        assert res["second_token_logprob"] == -0.2
        assert res["logprobs_mean"] == pytest.approx(-0.15)
        assert res["logprobs_std"] == pytest.approx(0.05)
        assert res["entropy"] > 0
        assert 0 < res["confidence"] <= 1.0

@pytest.mark.asyncio
async def test_forward_request_exception_propagation():
    adapter = LiteLLMAdapter(model_name="test")
    req = RoutingRequest(query="hi", parameters={})
    with patch('src.adapters.litellm_adapter.litellm.acompletion', side_effect=RuntimeError("API Down")):
        with pytest.raises(RuntimeError, match="API Down"):
            await adapter.forward_request(req)

@pytest.mark.asyncio
async def test_get_model_info():
    """Verify that LiteLLMAdapter exposes correct metadata."""
    # Case 1: Full config
    adapter1 = LiteLLMAdapter(model_name="test_model", api_key="key123", base_url="http://test")
    info1 = adapter1.get_model_info()
    assert info1["model_name"] == "test_model"
    assert info1["provider"] == "litellm"
    assert info1["base_url"] == "http://test"
    assert info1["api_key_configured"] is True
    # Ensure raw key not leaked
    assert "key123" not in str(info1)

    # Case 2: No API key
    adapter2 = LiteLLMAdapter(model_name="test_model_no_key")
    info2 = adapter2.get_model_info()
    assert info2["api_key_configured"] is False
