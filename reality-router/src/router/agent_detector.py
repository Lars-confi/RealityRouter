"""
Agent detection, interaction mode classification, observation analysis,
and trajectory tracking for Reality Router ladder escalation.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

CODING_AGENT_TOOL_NAMES = {
    "bash",
    "shell",
    "terminal",
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "find_path",
    "list_directory",
    "list_dir",
    "git",
    "test",
    "build",
    "run",
    "command",
    "exec",
    "execute",
    "file_search",
    "read",
    "write",
    "edit",
    "patch",
    "glob",
    "view",
}


def detect_interaction_mode(
    request: Any, settings: Optional[Any] = None
) -> Tuple[str, float]:
    """
    Detect the interaction mode of an incoming request.

    Returns:
        (interaction_mode, agent_confidence)
        interaction_mode: "tool_agent", "single_turn", or "unknown"
        agent_confidence: float between 0.0 and 1.0
    """
    # 1. Check settings overrides
    if settings is not None:
        handling = getattr(settings, "agent_handling", "auto")
        if isinstance(handling, str):
            handling_lower = handling.strip().lower()
            if handling_lower in ("force_agent", "tool_agent"):
                return "tool_agent", 1.0
            elif handling_lower in ("force_single_turn", "single_turn"):
                return "single_turn", 1.0

    # 2. Extract request parameters and messages
    params: Dict[str, Any] = {}
    if isinstance(request, dict):
        params = request.get("parameters") or request
        messages = params.get("messages") or request.get("messages") or []
        tools = params.get("tools") or request.get("tools") or []
        functions = params.get("functions") or request.get("functions") or []
        tool_choice = params.get("tool_choice") or request.get("tool_choice")
        query = request.get("query", "")
    else:
        params = getattr(request, "parameters", {}) or {}
        messages = params.get("messages") or getattr(request, "messages", []) or []
        tools = params.get("tools") or getattr(request, "tools", []) or []
        functions = params.get("functions") or getattr(request, "functions", []) or []
        tool_choice = params.get("tool_choice") or getattr(request, "tool_choice", None)
        query = getattr(request, "query", "")

    # 3. Inspect tools and function definitions (Turn 1 provisional agent mode)
    if tools or functions or (tool_choice and tool_choice != "none"):
        tool_names = []
        if isinstance(tools, list):
            for t in tools:
                if isinstance(t, dict):
                    fn = t.get("function")
                    if isinstance(fn, dict) and "name" in fn:
                        tool_names.append(str(fn["name"]).lower())
                    elif "name" in t:
                        tool_names.append(str(t["name"]).lower())
        if isinstance(functions, list):
            for f in functions:
                if isinstance(f, dict) and "name" in f:
                    tool_names.append(str(f["name"]).lower())
                elif isinstance(f, dict) and "function" in f:
                    fn = f["function"]
                    if isinstance(fn, dict) and "name" in fn:
                        tool_names.append(str(fn["name"]).lower())

        # Check if any tool matches coding/agent tool signatures
        for name in tool_names:
            if any(agent_tool in name for agent_tool in CODING_AGENT_TOOL_NAMES):
                return "tool_agent", 1.0

        return "tool_agent", 1.0 if tool_names else 0.95

    # 4. Inspect message history for tool interactions
    if isinstance(messages, list) and messages:
        for msg in messages:
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            if role in ("tool", "function"):
                return "tool_agent", 1.0

            if msg.get("tool_calls") or msg.get("function_call"):
                return "tool_agent", 1.0

            if msg.get("tool_call_id") or "function_call_output" in msg:
                return "tool_agent", 1.0

            name = msg.get("name")
            if name and isinstance(name, str):
                if any(agent_tool in name.lower() for agent_tool in CODING_AGENT_TOOL_NAMES):
                    return "tool_agent", 1.0

        # Non-tool conversational messages
        return "single_turn", 0.9

    if query and len(str(query).strip()) > 0:
        return "single_turn", 0.8

    return "unknown", 0.0


def _extract_observation_texts(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract tool / observation response text from messages."""
    texts = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("tool", "function") or msg.get("tool_call_id") or "function_call_output" in msg:
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and "text" in p:
                        parts.append(str(p["text"]))
                    elif isinstance(p, str):
                        parts.append(p)
                texts.append("\n".join(parts))
        elif role == "user":
            content = str(msg.get("content", ""))
            if any(
                prefix in content
                for prefix in ["[Action Result", "Command output:", "Observation:"]
            ):
                texts.append(content)
    return texts


def classify_observation(messages: List[Dict[str, Any]]) -> str:
    """
    Classify the observation state from recent message history.

    Returns:
        "success", "progress", "failure", "stagnation", or "unknown"
    """
    if not messages or not isinstance(messages, list):
        return "unknown"

    # Guard: only classify as observation when the latest message is itself an observation
    latest_message = messages[-1]
    if not isinstance(latest_message, dict):
        return "unknown"
    latest_role = latest_message.get("role", "")
    latest_content = str(latest_message.get("content", ""))
    is_observation = (
        latest_role in ("tool", "function")
        or bool(latest_message.get("tool_call_id"))
        or "function_call_output" in latest_message
        or (
            latest_role == "user"
            and any(
                prefix in latest_content
                for prefix in ["[Action Result", "Command output:", "Observation:"]
            )
        )
    )
    if not is_observation:
        return "unknown"

    observation_texts = _extract_observation_texts(messages)
    if not observation_texts:
        return "unknown"

    # Stagnation check: repeated identical or near-identical observations
    if len(observation_texts) >= 2:
        last_obs = observation_texts[-1].strip()
        prev_obs = observation_texts[-2].strip()
        if last_obs and last_obs == prev_obs:
            return "stagnation"

    latest = observation_texts[-1]

    # Check success patterns first if explicitly 0 errors/failures or exit code 0
    success_patterns = [
        r"(?:exit\s+(?:code|status)|returncode|exited\s+with\s+code)\s*[:=]?\s*0\b",
        r"\[exit code:\s*0\]",
        r"\b0\s+failed\b",
        r"\b(?:All tests passed|test result: ok|OK \(tests=|\b0 failures,\s*0 errors\b|Build succeeded|Compilation successful)\b",
    ]

    if any(re.search(pattern, latest, re.IGNORECASE) for pattern in success_patterns):
        return "success"

    # Failure patterns
    failure_patterns = [
        # Non-zero exit code
        r"(?:exit\s+(?:code|status)|returncode|exited\s+with\s+code)\s*[:=]?\s*([1-9]\d*)",
        r"\[exit code:\s*([1-9]\d*)\]",
        r"\bfailed\s+with\s+exit\s+code\s+[1-9]\d*\b",
        # Test failures
        r"\b(?:FAILURES|AssertionError|test(?:s)?\s+failed|failures\s*=\s*[1-9]\d*|errors\s*=\s*[1-9]\d*)\b",
        r"\bFAILED\b",
        r"\b[1-9]\d*\s+failed\b",
        r"\bpytest:\s+error\b",
        r"FAIL:\s+\w+",
        # Tracebacks / Exceptions
        r"Traceback \(most recent call last\):",
        r"\b(?:[A-Z]\w*Error|[A-Z]\w*Exception):\s+.+",
        r"\b(?:NullPointerException|Segmentation fault|SIGSEGV|Panic:|panic:)\b",
        # Compiler & Build errors
        r"error\[E\d+\]",
        r"\b(?:compilation error|build failed|fatal error|undefined reference to|cannot find symbol|TS\d{4}:)\b",
    ]

    for pattern in failure_patterns:
        if re.search(pattern, latest, re.IGNORECASE):
            return "failure"

    # If there is observation content without errors, consider it progress
    if latest.strip():
        return "progress"

    return "unknown"


class TrajectoryTracker:
    """
    Maintains in-memory session state for agent execution trajectories:
    - consecutive_failures
    - consecutive_stagnation
    - recent_outcomes
    - pending_observation
    - cooldown_steps
    """

    def __init__(self, max_sessions: int = 1000, session_ttl_seconds: float = 3600.0):
        self.consecutive_failures: int = 0
        self.consecutive_stagnation: int = 0
        self.recent_outcomes: List[str] = []
        self.pending_observation: bool = False
        self.cooldown_steps: int = 0
        self.max_sessions: int = max(1, int(max_sessions))
        self.session_ttl_seconds: float = float(session_ttl_seconds)
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _prune_sessions(self, now: float, *, reserve_slot: bool = False) -> None:
        if self.session_ttl_seconds > 0:
            for sid in [k for k, v in self._sessions.items() if (now - float(v.get("last_updated", 0.0))) > self.session_ttl_seconds]:
                del self._sessions[sid]
        limit = self.max_sessions - (1 if reserve_slot else 0)
        if len(self._sessions) > limit:
            oldest = sorted(self._sessions.items(), key=lambda kv: float(kv[1].get("last_updated", 0.0)))
            for sid, _ in oldest[: len(self._sessions) - max(0, limit)]:
                del self._sessions[sid]

    def get_session_state(self, session_id: str = "default") -> Dict[str, Any]:
        now = time.time()
        existing = self._sessions.get(session_id)
        if existing is not None:
            if self.session_ttl_seconds > 0 and (now - float(existing.get("last_updated", 0.0))) > self.session_ttl_seconds:
                del self._sessions[session_id]
            else:
                return existing
        self._prune_sessions(now, reserve_slot=True)
        self._sessions[session_id] = {
            "consecutive_failures": 0,
            "consecutive_stagnation": 0,
            "recent_outcomes": [],
            "pending_observation": False,
            "cooldown_steps": 0,
            "last_updated": now,
        }
        return self._sessions[session_id]

    def record_observation(self, outcome: str, session_id: str = "default") -> None:
        """Record an observation outcome for a session."""
        state = self.get_session_state(session_id)
        state["pending_observation"] = False
        state["recent_outcomes"].append(outcome)
        if len(state["recent_outcomes"]) > 20:
            state["recent_outcomes"].pop(0)

        if outcome == "failure":
            state["consecutive_failures"] += 1
            state["consecutive_stagnation"] = 0
        elif outcome == "stagnation":
            state["consecutive_stagnation"] += 1
        elif outcome in ("success", "progress"):
            state["consecutive_failures"] = 0
            state["consecutive_stagnation"] = 0
            if state["cooldown_steps"] > 0:
                state["cooldown_steps"] -= 1

        state["last_updated"] = time.time()

        # Sync top-level instance fields
        self.consecutive_failures = state["consecutive_failures"]
        self.consecutive_stagnation = state["consecutive_stagnation"]
        self.recent_outcomes = list(state["recent_outcomes"])
        self.pending_observation = state["pending_observation"]
        self.cooldown_steps = state["cooldown_steps"]

    def record_tool_call(self, session_id: str = "default") -> None:
        """Mark that a tool call was issued and an observation is now pending."""
        state = self.get_session_state(session_id)
        state["pending_observation"] = True
        state["last_updated"] = time.time()
        self.pending_observation = True

    def should_escalate(
        self, session_id: str = "default", failure_threshold: int = 2
    ) -> bool:
        """Determine if repeated failures or stagnation trigger escalation."""
        state = self.get_session_state(session_id)
        return (
            state["consecutive_failures"] >= failure_threshold
            or state["consecutive_stagnation"] >= failure_threshold
        )

    def reset(self, session_id: Optional[str] = None) -> None:
        """Reset state for a specific session or all sessions."""
        if session_id:
            if session_id in self._sessions:
                del self._sessions[session_id]
        else:
            self._sessions.clear()

        self.consecutive_failures = 0
        self.consecutive_stagnation = 0
        self.recent_outcomes = []
        self.pending_observation = False
        self.cooldown_steps = 0
