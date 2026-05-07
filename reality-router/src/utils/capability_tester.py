import json
import os
import asyncio
from typing import Dict, Optional
from src.models.routing import RoutingRequest
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

APP_HOME = os.getenv("REALITY_ROUTER_HOME", os.path.expanduser("~/.reality_router"))
CACHE_FILE = os.path.join(APP_HOME, "config", "capabilities.json")

class CapabilityManager:
    """Probes and caches model capabilities (Tools, Logprobs) without affecting DB metrics."""
    
    def __init__(self):
        self.capabilities = {}
        self._load()

    def _load(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    self.capabilities = json.load(f)
            except Exception:
                pass

    def _save(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.capabilities, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save capabilities: {e}")

    def get_capabilities(self, model_id: str) -> Optional[Dict]:
        return self.capabilities.get(model_id)

    async def probe_model(self, model_id: str, adapter) -> Dict:
        if model_id in self.capabilities:
            return self.capabilities[model_id]

        logger.info(f"Probing capabilities for {model_id}...")
        supports_tools = False
        supports_logprobs = False

        # Test 1: Tools + Logprobs
        req_tools = RoutingRequest(
            query="test",
            parameters={
                "model": model_id,
                "messages": [{"role": "user", "content": "Reply with exactly 'test', no other text."}],
                "max_tokens": 10,
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "dummy_tool",
                        "description": "dummy", "parameters": {"type": "object", "properties": {}}
                    }
                }],
                "logprobs": True,
                "top_logprobs": 2
            }
        )

        try:
            resp = await adapter.forward_request(req_tools)
            supports_tools = True
            if resp.get("logprobs_mean", 0.0) != 0.0 or resp.get("entropy", 0.0) != 0.0:
                supports_logprobs = True
        except Exception as e:
            # Fallback Test 2: No Tools, just Logprobs
            req_logprobs = RoutingRequest(
                query="test",
                parameters={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Reply with exactly 'test', no other text."}],
                    "max_tokens": 10,
                    "logprobs": True,
                    "top_logprobs": 2
                }
            )
            try:
                resp = await adapter.forward_request(req_logprobs)
                supports_tools = False
                if resp.get("logprobs_mean", 0.0) != 0.0 or resp.get("entropy", 0.0) != 0.0:
                    supports_logprobs = True
            except Exception:
                # Fallback Test 3: Vanilla request (just to see if it works at all)
                req_vanilla = RoutingRequest(
                    query="test",
                    parameters={
                        "model": model_id,
                        "messages": [{"role": "user", "content": "Reply with exactly 'test', no other text."}],
                        "max_tokens": 10
                    }
                )
                try:
                    await adapter.forward_request(req_vanilla)
                except Exception as e2:
                    logger.warning(f"Model {model_id} completely failed capability probe: {e2}")

        caps = {
            "supports_tools": supports_tools,
            "supports_logprobs": supports_logprobs,
            "tested": True
        }
        self.capabilities[model_id] = caps
        self._save()
        logger.info(f"Probed {model_id}: Tools={supports_tools}, Logprobs={supports_logprobs}")
        return caps

capability_manager = CapabilityManager()
