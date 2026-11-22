"""
Unit tests for the CapabilityManager in `src/utils/capability_tester.py`.
"""
import os
import json
import pytest
import asyncio
from unittest.mock import MagicMock, patch
from src.utils.capability_tester import CapabilityManager
from src.models.routing import RoutingRequest

class MockAdapter:
    def __init__(self, behavior="success"):
        self.behavior = behavior
    
    async def forward_request(self, request):
        # Explicitly handle 'partial_tool' behavior specially: tools_probe should fail
        if self.behavior == "partial_tool":
            # Simulate a conditional failing response based on test-specific query
            # This mock mimics a partial-tooling adapter that fails the first tool probe
            # but may succeed on logprobs-only probe
            if hasattr(request, 'parameters') and 'tools' in request.parameters:
                raise Exception("Partial tool probe failed")
            return {"logprobs_mean": 0.5, "entropy": 0.2}
        if self.behavior == "fail":
            raise Exception("Probe failed")
        if self.behavior == "logprobs_only":
            return {"logprobs_mean": 0.5, "entropy": 0.2}
        if self.behavior == "tools_only":
            return {"logprobs_mean": 0.0, "entropy": 0.0}
        return {"logprobs_mean": 0.5, "entropy": 0.2}

@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory and override the global CACHE_FILE."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cache_file = config_dir / "capabilities.json"
    
    import src.utils.capability_tester as ct
    original_cache = ct.CACHE_FILE
    ct.CACHE_FILE = str(cache_file)
    yield cache_file
    ct.CACHE_FILE = original_cache

@pytest.mark.asyncio
async def test_capability_manager_cache_hit(temp_config_dir):
    """Test that cached values are returned without calling the adapter."""
    with open(temp_config_dir, "w") as f:
        json.dump({"model-1": {"supports_tools": True, "supports_logprobs": False, "tested": True}}, f)
    
    manager = CapabilityManager()
    adapter = MagicMock()
    
    # We use probe_model which should hit the cache and return without calling adapter
    caps = await manager.probe_model("model-1", adapter)
    assert caps["supports_tools"] is True
    assert caps["supports_logprobs"] is False
    adapter.forward_request.assert_not_called()

@pytest.mark.asyncio
async def test_capability_manager_probing_logic(temp_config_dir):
    """Test that probing correctly identifies capabilities."""
    manager = CapabilityManager()
    
    adapter_both = MockAdapter(behavior="success")
    caps_both = await manager.probe_model("model-both", adapter_both)
    assert caps_both["supports_tools"] is True
    assert caps_both["supports_logprobs"] is True
    
    adapter_tools = MockAdapter(behavior="tools_only")
    caps_tools = await manager.probe_model("model-tools", adapter_tools)
    assert caps_tools["supports_tools"] is True
    assert caps_tools["supports_logprobs"] is False
    
    adapter_partial = MockAdapter(behavior="partial_tool")
    caps_partial = await manager.probe_model("model-partial", adapter_partial)
    assert caps_partial["supports_tools"] is False
    assert caps_partial["supports_logprobs"] is True
    
    # Both fail
    class BothFailAdapter(MockAdapter):
        async def forward_request(self, request):
            raise Exception("Probe failed")
    adapter_both_fail = BothFailAdapter(behavior="fail")
    caps_both_fail = await manager.probe_model("model-both-fail", adapter_both_fail)
    assert caps_both_fail["supports_tools"] is False
    assert caps_both_fail["supports_logprobs"] is False

@pytest.mark.asyncio
async def test_capability_manager_persistence(temp_config_dir):
    """Test that probes are persisted and reloaded."""
    adapter = MockAdapter(behavior="success")
    manager1 = CapabilityManager()
    await manager1.probe_model("model-persist", adapter)
    
    manager2 = CapabilityManager()
    caps = manager2.get_capabilities("model-persist")
    assert caps is not None
    assert caps["supports_tools"] is True

@pytest.mark.asyncio
async def test_capability_manager_corrupt_cache(temp_config_dir):
    """Test recovery from a corrupt JSON cache file."""
    with open(temp_config_dir, "w") as f:
        f.write("NOT JSON")
    
    manager = CapabilityManager()
    assert manager.capabilities == {}
