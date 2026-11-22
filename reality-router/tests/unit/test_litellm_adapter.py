"""
Unit tests for LiteLLM adapter.
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock
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
    
    # Case 1: Content empty, fallback to reasoning
    mock_msg = MagicMock()
    mock_msg.content = ""
    mock_msg.role = "assistant"
    mock_msg.reasoning_content = "thought_process"
    mock_resp = MagicMock(choices=[MagicMock(message=mock_msg, finish_reason="stop")])
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=mock_resp):
        res = await adapter.forward_request(req)
        assert res["text"] == "thought_process"

    # Case 2: provider_specific_fields fallback
    mock_msg.reasoning_content = ""
    mock_msg.provider_specific_fields = {"thought": "deep_thought"}
    res = await adapter.forward_request(req)
    assert res["text"] == "deep_thought"

@pytest.mark.asyncio
async def test_forward_request_normalization():
    adapter = LiteLLMAdapter(model_name="test")
    req = RoutingRequest(query="hi", parameters={})
    
    # Mock complex response
    mock_msg = MagicMock(content="ok", role="assistant")
    mock_fn = MagicMock()
    mock_fn.name = "t"
    mock_fn.arguments = "{}"
    mock_tc = MagicMock()
    mock_tc.id = "call-1"
    mock_tc.type = "function"
    mock_tc.function = mock_fn
    mock_msg.tool_calls = [mock_tc]
    
    mock_usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    
    mock_logprobs = MagicMock(content=MagicMock(logprob=0.5)) # Simplified
    
    mock_resp = MagicMock(
        choices=[MagicMock(message=mock_msg, finish_reason="stop", logprobs=mock_logprobs)],
        usage=mock_usage
    )
    
    with patch('src.adapters.litellm_adapter.litellm.acompletion', return_value=mock_resp):
        res = await adapter.forward_request(req)
        assert res["usage"]["prompt_tokens"] == 10
        assert len(res["tool_calls"]) == 1
        assert res["tool_calls"][0]["function"]["name"] == "t"

@pytest.mark.asyncio
async def test_forward_request_exception_propagation():
    adapter = LiteLLMAdapter(model_name="test")
    req = RoutingRequest(query="hi", parameters={})
    with patch('src.adapters.litellm_adapter.litellm.acompletion', side_effect=RuntimeError("API Down")):
        with pytest.raises(RuntimeError, match="API Down"):
            await adapter.forward_request(req)
