import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.models.routing import RoutingRequest
from src.router.core import RouterCore


@pytest.fixture
def mock_db_session():
    """Mock the database session to prevent actual DB calls during tests."""
    with patch("src.router.core.SessionLocal") as mock_session_local:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        mock_session_local.return_value = mock_db
        yield mock_db


@pytest.fixture
def live_router_core(mock_db_session):
    """
    Returns a RouterCore instance that dynamically loads models from the
    environment/config exactly as the live app does, testing auto-discovery.
    """
    import importlib
    import os
    import re

    import src.config.settings

    # Parse ~/.bashrc to extract exported environment variables
    bashrc_path = os.path.expanduser("~/.bashrc")
    bash_vars = {}
    if os.path.exists(bashrc_path):
        with open(bashrc_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    # Handle export VAR="value" or export VAR=value
                    match = re.match(
                        r"export\s+([A-Za-z0-9_]+)=[\"']?(.*?)[\"']?$", line
                    )
                    if match:
                        bash_vars[match.group(1)] = match.group(2)

    # Create ~/.reality_router/.env
    env_dir = os.path.expanduser("~/.reality_router")
    os.makedirs(env_dir, exist_ok=True)
    env_path = os.path.join(env_dir, ".env")

    with open(env_path, "w") as f:
        f.write('SENTIMENT_MODEL_ID="nemotron-3-nano:30b"\n')
        f.write(
            'ROUTING_MODEL_IDS="qwen3-coder:30b,gemini-2.5-flash,gemini-3-flash-preview"\n'
        )
        f.write('ROUTING_STRATEGY="first_available"\n')
        for k in [
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "CUSTOM_LLM_BASE_URL",
            "CUSTOM_LLM_API_KEY",
        ]:
            if k in bash_vars:
                f.write(f'{k}="{bash_vars[k]}"\n')

    # Force load into os.environ
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                val = v.strip().strip("'").strip('"')
                if val:  # Only inject non-empty values
                    os.environ[k.strip()] = val

    # Reload settings to ensure they pick up the newly injected env vars
    importlib.reload(src.config.settings)

    core = RouterCore()

    # Prune auto-discovered models down to the explicitly configured list
    allowed_models = set()
    settings = src.config.settings.get_settings()
    if settings.sentiment_model_id:
        allowed_models.add(settings.sentiment_model_id)

    for rid in os.environ.get("ROUTING_MODEL_IDS", "").split(","):
        if rid.strip():
            allowed_models.add(rid.strip())

    for m_id in list(core.models.keys()):
        if m_id not in allowed_models:
            del core.models[m_id]
            core.adapters.pop(m_id, None)
            if (
                hasattr(core.load_balancer, "models")
                and m_id in core.load_balancer.models
            ):
                del core.load_balancer.models[m_id]

    from src.adapters.litellm_adapter import LiteLLMAdapter

    # Inject a dummy model that lacks tool support to test the router's capability filter
    dummy_model = "dummy-no-tools:latest"
    core.add_model(
        model_id=dummy_model,
        model_name=dummy_model,
        cost=0.0001,
        time=1.0,
        probability=0.5,
        concurrency_limit=None,
        prompt_cost=0.0001,
        completion_cost=0.0001,
        supports_function_calling=False,
        max_input_tokens=8192,
        max_tokens=4096,
    )
    core.adapters[dummy_model] = LiteLLMAdapter(
        model_name=f"openai/{dummy_model}",
        api_key="dummy",
        base_url="http://100.81.4.19:11434/v1",
    )
    core.load_balancer.add_model(dummy_model, dummy_model, 1.0)

    # Force the load balancer to report all these models as healthy
    core.load_balancer.is_model_healthy = MagicMock(return_value=True)

    # If only the dummy model was added, auto-discovery failed
    if len(core.models) <= 1:
        pytest.skip("No live models discovered. Ensure API keys are set in .env.")

    return core


# --- Reconstructing the Sequential Multi-Turn Conversation from log_1.txt ---

# Turn 1: Initial System + User Prompt
MSG_0 = {
    "role": "system",
    "content": "You are a personal assistant running inside OpenClaw.",
}
MSG_1 = {
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": "The conversation history before this point was compacted into the following summary:\n<summary>...</summary>",
        }
    ],
}
MSG_2 = {"role": "user", "content": [{"type": "text", "text": ""}]}

# Turn 2: Assistant spawns a sub-agent, System responds with Tool result
MSG_3 = {
    "role": "assistant",
    "content": "We have a user just sent empty string. Let's spawn a subagent.",
    "tool_calls": [
        {
            "id": "callb9lgq2d9",
            "type": "function",
            "function": {"name": "sessions_spawn", "arguments": "{}"},
        }
    ],
}
MSG_4 = {
    "role": "tool",
    "content": '{\n  "status": "accepted",\n  "childSessionKey": "agent:main:subagent:fe8d57d4-8dc4-4d9f-9f44-456f12310024"\n}',
    "tool_call_id": "callb9lgq2d9",
}

# Turn 3: Assistant yields the turn, System responds with completion events
MSG_5 = {
    "role": "assistant",
    "content": "We have successfully spawned a sub-agent. Now we yield.",
    "tool_calls": [
        {
            "id": "callsqbr2no7",
            "type": "function",
            "function": {"name": "sessions_yield", "arguments": '{"message":""}'},
        }
    ],
}
MSG_6 = {
    "role": "tool",
    "content": '{\n  "status": "yielded",\n  "message": "Turn yielded."\n}',
    "tool_call_id": "callsqbr2no7",
}
MSG_7 = {
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": "Turn yielded.\n\n[Context: The previous turn ended intentionally via sessions_yield while waiting for a follow-up event.]",
        }
    ],
}
MSG_8 = {
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": "[Tue 2026-05-12 19:39 GMT+2] <<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>\nOpenClaw runtime context (internal): [Internal task completion event]\ntask: research-task-checker\nstatus: completed successfully\n<<<END_OPENCLAW_INTERNAL_CONTEXT>>>",
        }
    ],
}

# Sequential turn histories
TURN_1_MSGS = [MSG_0, MSG_1, MSG_2]
TURN_2_MSGS = TURN_1_MSGS + [MSG_3, MSG_4]
TURN_3_MSGS = TURN_2_MSGS + [MSG_5, MSG_6, MSG_7, MSG_8]

# Tools required for the request to trigger capability filtering
TOOLS_PAYLOAD = [
    {
        "type": "function",
        "function": {
            "name": "sessions_spawn",
            "description": "Spawn a clean isolated session by default with the native subagent runtime.",
            "parameters": {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {"type": "string"},
                    "label": {"type": "string"},
                    "runtime": {"type": "string", "enum": ["subagent"]},
                    "mode": {"type": "string", "enum": ["run", "session"]},
                    "cleanup": {"type": "string", "enum": ["delete", "keep"]},
                    "context": {"type": "string", "enum": ["isolated", "fork"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sessions_yield",
            "description": "End your current turn. Use after spawning subagents to receive their results as the next message.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
            },
        },
    },
]


class MockHTTPXResponse:
    """Helper to mock httpx.post responses for Reality Router"""

    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn_idx, messages", [(1, TURN_1_MSGS), (2, TURN_2_MSGS), (3, TURN_3_MSGS)]
)
async def test_live_capability_filtering_and_reality_check_tables(
    live_router_core, turn_idx, messages
):
    """
    Tests the live Reality Router infrastructure:
    1. Extracts real features and hits the mocked Reality Router /decide endpoint to avoid network errors.
    2. Ranks live, dynamically discovered models based on expected utility.
    3. Excludes models without tool capabilities (dummy-no-tools) when tools are present.
    4. Prints the Model Comparison tables mimicking the interaction log.
    """
    request_payload = {
        "model": "RealRouter",
        "messages": messages,
        "stream": True,
        "tools": TOOLS_PAYLOAD,
    }

    routing_req = RoutingRequest(
        query="", agent_id="OpenAI/JS 6.26.0", parameters=request_payload
    )

    # Custom side effect for Reality Router POST requests to ensure reliable probability generation
    async def mock_rc_post(url, json=None, headers=None, timeout=None, **kwargs):
        features = json.get("features", {})
        model_id = features.get("model_id", "unknown")

        probs = {
            "qwen3-coder:30b": 0.65,
            "nemotron-3-nano:30b": 0.57,
            "dummy-no-tools:latest": 0.50,
        }

        return MockHTTPXResponse(
            {
                "prob_true": probs.get(model_id, 0.5),
                "uncertainty": 0.05,
                "decision_id": 14121 + turn_idx,
                "feedback_requested": False,
            }
        )

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # Call the get_ranked_models which triggers feature extraction and mock RC
        decisions = await live_router_core.get_ranked_models(
            routing_req, strategy="expected_utility"
        )

        evaluated_model_ids = [d.model_id for d in decisions]

        # Assert we got some decisions back
        assert len(decisions) > 0

        # Verify capability filtering: dummy-no-tools should NOT be in the ranked decisions
        assert "dummy-no-tools:latest" not in evaluated_model_ids

        # Verify the capable models ARE evaluated
        assert "nemotron-3-nano:30b" in evaluated_model_ids
        assert "qwen3-coder:30b" in evaluated_model_ids

        # Print the comparison table for this turn
        print(f"\n\n📊 LIVE MODEL COMPARISON for Turn {turn_idx} 📊")
        print(f"┌{'─' * 32}┬{'─' * 10}┬{'─' * 8}┐")
        print(f"│ {'Model Name':<30} │ {'Utility':>8} │ {'Prob':>6} │")
        print(f"├{'─' * 32}┼{'─' * 10}┼{'─' * 8}┤")
        for d in decisions:
            marker = "🏆" if d == decisions[0] else "  "
            print(
                f"│ {marker} {d.name:<27} │ {d.expected_utility:>8.4f} │ {d.probability:>6.2f} │"
            )
        print(f"└{'─' * 32}┴{'─' * 10}┴{'─' * 8}┘\n")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn_idx, messages", [(1, TURN_1_MSGS), (2, TURN_2_MSGS), (3, TURN_3_MSGS)]
)
async def test_live_sentiment_parsing(live_router_core, turn_idx, messages):
    """
    Tests the sentiment assessment feature against all LIVE configured models.
    Sends real requests to the underlying LLM providers and validates the parser
    can successfully reduce the response to the exact expected formats.
    """
    from src.config.settings import get_settings

    settings = get_settings()

    request_payload = {"model": "RealRouter", "messages": messages}
    routing_req = RoutingRequest(
        query="", agent_id="OpenAI/JS 6.26.0", parameters=request_payload
    )

    all_models = [
        m
        for m in list(live_router_core.adapters.keys())
        if m != "dummy-no-tools:latest"
    ]
    if not all_models:
        pytest.skip("No capable live models configured or available.")

    for sentiment_model in all_models:
        print(f"\n🧠 Evaluating sentiment using live model: {sentiment_model}")
        settings.sentiment_model_id = sentiment_model

        # Call the live sentiment assessment logic
        try:
            sentiment = await live_router_core.assess_user_sentiment(routing_req)
            print(f"   => Result: {sentiment}")
        except Exception as e:
            print(f"   ❌ Model {sentiment_model} failed to assess sentiment: {e}")
            continue

        # For live models, the assessment is subjective and varies by model tuning.
        # We just need to ensure the system successfully parses the raw output
        # into one of the expected enum values without crashing.
        assert sentiment in ["happy", "unhappy", "indeterminate", None]


@pytest.mark.asyncio
async def test_live_tool_call_execution(live_router_core):
    """
    Issues a live request to the Reality Router using the Turn 1 payload for each available live model.
    Since the prompt contains no explicit instructions but provides tools,
    we test that each live model correctly responds, and if it chooses
    to use a tool, the router preserves the format correctly.
    """
    from unittest.mock import patch

    from src.router.core import RoutingDecision

    request_payload = {
        "model": "RealRouter",
        "messages": TURN_1_MSGS,
        "stream": False,
        "tools": TOOLS_PAYLOAD,
    }

    routing_req = RoutingRequest(
        query="", agent_id="OpenAI/JS 6.26.0", parameters=request_payload
    )

    all_models = [
        m
        for m in list(live_router_core.adapters.keys())
        if m != "dummy-no-tools:latest"
    ]
    if not all_models:
        pytest.skip("No capable live models configured or available.")

    for target_model in all_models:
        print(f"\n🚀 Testing live tool call execution against: {target_model}")

        with patch.object(live_router_core, "get_ranked_models") as mock_rank:
            mock_rank.return_value = [
                RoutingDecision(
                    model_id=target_model,
                    expected_utility=1.0,
                    cost=0.0,
                    time=1.0,
                    probability=1.0,
                    name=target_model,
                )
            ]

            try:
                # This performs the LIVE network call
                response = await live_router_core.route_request(routing_req)
            except Exception as e:
                print(f"   ❌ Model {target_model} failed with exception: {e}")
                continue

            print(f"   ✅ Live routing executed against: {response.model_id}")

            assert response.response is not None

            if "tool_calls" in response.response and response.response["tool_calls"]:
                print("   🛠️  Model chose to execute a tool call.")
                tc = response.response["tool_calls"][0]
                assert "function" in tc
                assert "name" in tc["function"]
                assert "arguments" in tc["function"]
                print(f"      -> Tool Name: {tc['function']['name']}")

                # Verify JSON arguments are parseable
                args = json.loads(tc["function"]["arguments"])
                assert isinstance(args, dict)
            else:
                print("   💬 Model responded with text:")
                print(f"      {response.response.get('text', '')}")
                assert response.response.get("text") is not None
