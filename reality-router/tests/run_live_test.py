import asyncio
import json
import os
import sys

# Ensure src is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.adapters.litellm_adapter import LiteLLMAdapter
from src.models.routing import RoutingRequest
from src.router.core import RouterCore


async def run_tests():
    print("--- Initializing Live Router Core ---")
    core = RouterCore()
    core.models = {}
    core.adapters = {}

    # Define the models we want to test live
    ollama_url = "http://100.81.4.19:11434/v1"

    models = [
        {"id": "nemotron-3-nano:30b", "provider": "openai", "url": ollama_url, "key": "dummy"},
        {"id": "qwen3-coder:30b", "provider": "openai", "url": ollama_url, "key": "dummy"},
        # We can add a fallback gemini test structure here, but to avoid API Key issues,
        # we stick to the local models we KNOW are working from previous logs!
    ]

    for m in models:
        core.add_model(
            model_id=m["id"],
            model_name=m["id"],
            cost=0.001,
            time=1.0,
            probability=0.8,
            concurrency_limit=None,
            prompt_cost=0.001,
            completion_cost=0.001,
            supports_function_calling=True,
            max_input_tokens=32000,
            max_tokens=8192,
        )
        core.adapters[m["id"]] = LiteLLMAdapter(
            model_name=f"{m['provider']}/{m['id']}",
            api_key=m["key"],
            base_url=m["url"]
        )
        core.load_balancer.add_model(m["id"], m["id"], 1.0)

    # We must patch get_ranked_models so it doesn't fail on network issues to the real Reality Router API
    # if it's not running locally, while still evaluating the models.
    from unittest.mock import patch

    from src.router.core import RoutingDecision

    print("\n--- Running Live Tool Call Test ---")

    # Simple prompt from Turn 1
    messages = [
        {
            "role": "system",
            "content": "You are a personal assistant running inside OpenClaw.",
        },
        {"role": "user", "content": "Hello. I need help."},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "sessions_spawn",
                "parameters": {
                    "type": "object",
                    "properties": {"task": {"type": "string"}},
                },
            },
        }
    ]

    req = RoutingRequest(
        query="Hello. I need help.",
        agent_id="test",
        parameters={"model": "RealRouter", "messages": messages, "tools": tools},
    )

    for m in models:
        print(f"\n[Testing Model: {m['id']}]")
        with patch.object(core, "get_ranked_models") as mock_rank:
            mock_rank.return_value = [
                RoutingDecision(
                    model_id=m["id"],
                    expected_utility=1.0,
                    cost=0,
                    time=1,
                    probability=1,
                    name=m["id"],
                )
            ]
            try:
                resp = await core.route_request(req)
                if "tool_calls" in resp.response and resp.response["tool_calls"]:
                    print(
                        f"Result: SUCCESS! Model returned a Tool Call: {resp.response['tool_calls'][0]['function']['name']}"
                    )
                else:
                    text = resp.response.get("text", "").replace("\n", " ")
                    print(f"Result: SUCCESS! Model returned Text: {text[:80]}...")
            except Exception as e:
                print(f"Result: FAILED! {e}")


if __name__ == "__main__":
    asyncio.run(run_tests())
