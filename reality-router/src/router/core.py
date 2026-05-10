"""
Core routing logic implementing Expected Utility Theory framework
"""

import ast
import asyncio
import datetime
import hashlib
import json
import math
import re
import statistics
import time
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config.settings import get_settings, load_models_from_config
from src.models.database import RoutingLog, SessionLocal, get_db, init_db
from src.models.routing import RoutingRequest, RoutingResponse
from src.router.load_balancer import load_balancer
from src.router.metrics import metrics_collector
from src.utils.capability_tester import capability_manager
from src.utils.logger import setup_logger
from src.utils.pricing import pricing_manager

logger = setup_logger(__name__)

router = APIRouter()

# Reality Check API Configuration - Hardcoded per v1.0.0.0 Spec
REALITY_ROUTING_URL = (
    "https://llmrouter-api.jollysand-1b9ed42e.swedencentral.azurecontainerapps.io"
)
REALITY_REROUTING_URL = (
    "https://llmrerouter-api.jollysand-1b9ed42e.swedencentral.azurecontainerapps.io"
)
REALITY_CHECK_ROUTING_KEY = "f7a2b9c8d1e3f5a2b9c8d1e3f5a2b9c8"
REALITY_CHECK_REROUTING_KEY = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

# Infrastructure failure detection patterns
INFRA_FAILURE_PATTERNS = [
    "service_unavailable",
    "overloaded",
    "rate_limit",
    "connection error",
    "timeout",
    "503",
    "429",
    "high demand",
]


def get_reality_check_key(url: str) -> str:
    if url == REALITY_ROUTING_URL:
        return REALITY_CHECK_ROUTING_KEY
    return REALITY_CHECK_REROUTING_KEY


class ModelMetrics(BaseModel):
    """Model performance metrics"""

    model_id: str
    cost: float  # Cost per million tokens
    time: float  # Average response time in seconds
    probability: float  # Success probability (0-1)
    name: str


class RoutingDecision(BaseModel):
    """Routing decision with expected utility"""

    model_id: str
    expected_utility: float
    cost: float
    time: float
    probability: float
    uncertainty: float = 0.0
    name: str
    reality_check_id: Optional[Union[int, str]] = None
    feedback_required: bool = False
    is_random_exploration: bool = False


class ExpectedUtilityCalculator:
    """Calculator for Expected Utility Theory implementation"""

    def __init__(
        self,
        reward: float = 1.0,
        cost_sensitivity: float = 0.5,
        time_sensitivity: float = 0.5,
    ):
        self.reward = reward
        self.cost_sensitivity = cost_sensitivity
        self.time_sensitivity = time_sensitivity

        if time_sensitivity is None:
            self.time_sensitivity = 1.0 - cost_sensitivity

    def calculate_expected_utility(
        self, cost: float, time: float, probability: float
    ) -> float:
        return (
            probability * self.reward
            - self.cost_sensitivity * cost
            - self.time_sensitivity * time
        )


class RouterCore:
    """Main router core implementing Expected Utility Theory"""

    def __init__(self):
        self.models = {}
        self.metrics = {}
        self.models_to_probe = []
        settings = get_settings()
        self.utility_calculator = ExpectedUtilityCalculator(
            reward=settings.reward,
            cost_sensitivity=settings.cost_sensitivity,
            time_sensitivity=settings.time_sensitivity,
        )
        self.adapters = {}
        self.load_balancer = load_balancer
        self.concurrency_limits = {}  # Map of model_id to asyncio.Semaphore
        self.active_sessions = {}

        init_db()
        self.load_configured_models()

        logger.info("Router core initialized with Expected Utility Theory framework")

    def load_configured_models(self):
        """Load models from configuration and dynamically discover them"""
        try:
            settings = get_settings()

            # Load static user models if any
            config_models = load_models_from_config()
            for model_id, model_info in config_models.items():
                if model_id in settings.disabled_models:
                    continue
                (
                    p_cost,
                    c_cost,
                    supports_function_calling,
                    max_input_tokens,
                    max_tokens,
                ) = pricing_manager.get_model_pricing(model_id)
                self.add_model(
                    model_id=model_id,
                    model_name=model_info.get("name", model_id),
                    cost=model_info.get(
                        "cost", (p_cost + c_cost) / 2 if p_cost is not None else 0.0
                    ),
                    time=model_info.get("time", 1.0),
                    probability=model_info.get("probability", 0.8),
                    concurrency_limit=model_info.get("concurrency_limit")
                    or model_info.get("thread_limit"),
                    prompt_cost=model_info.get("prompt_cost")
                    if model_info.get("prompt_cost") is not None
                    else p_cost,
                    completion_cost=model_info.get("completion_cost")
                    if model_info.get("completion_cost") is not None
                    else c_cost,
                    supports_function_calling=supports_function_calling,
                    max_input_tokens=model_info.get("max_input_tokens")
                    or max_input_tokens,
                    max_tokens=model_info.get("max_tokens") or max_tokens,
                )
                self.load_balancer.add_model(
                    model_id, model_info.get("name", model_id), 1.0
                )
                base_url = model_info.get("base_url", "")

                from src.adapters.litellm_adapter import LiteLLMAdapter

                model_name_for_adapter = model_info.get("model", model_id)

                if "gemini" in model_id.lower() or "generativelanguage" in base_url:
                    if model_name_for_adapter.startswith("models/"):
                        model_name_for_adapter = model_name_for_adapter[7:]
                    if not model_name_for_adapter.startswith("gemini/"):
                        model_name_for_adapter = f"gemini/{model_name_for_adapter}"
                elif (
                    "11434" in base_url
                    or "localhost" in base_url
                    or "127.0.0.1" in base_url
                ):
                    if not model_name_for_adapter.startswith("openai/"):
                        model_name_for_adapter = f"openai/{model_name_for_adapter}"

                self.adapters[model_id] = LiteLLMAdapter(
                    model_name=model_name_for_adapter,
                    api_key=model_info.get("api_key"),
                    base_url=base_url if base_url else None,
                )

            # Auto-Discover Dynamic Models
            if not settings.enable_auto_discovery:
                logger.info("Auto-discovery is disabled.")
                return

            logger.info("Auto-discovering models from configured providers...")

            sentiment_model_id = settings.sentiment_model_id

            # 1. Custom/Local Models (Ollama or Generic)
            custom_url = settings.custom_llm_base_url
            custom_key = settings.custom_llm_api_key or "dummy"
            if custom_url:
                try:
                    if "11434" in custom_url:  # Ollama
                        ollama_url = (
                            custom_url.replace("/v1", "/api/tags")
                            if custom_url.endswith("/v1")
                            else f"{custom_url}/api/tags"
                        )
                        resp = httpx.get(ollama_url, timeout=3)
                        if resp.status_code == 200:
                            ollama_models = resp.json().get("models", [])
                            logger.info(
                                f"Ollama discovery: found {len(ollama_models)} models"
                            )
                            for m in ollama_models:
                                name = m.get("name")
                                if (
                                    name
                                    and name not in self.models
                                    and (
                                        name not in settings.disabled_models
                                        or name == sentiment_model_id
                                    )
                                ):
                                    base = (
                                        custom_url
                                        if custom_url.endswith("/v1")
                                        else f"{custom_url}/v1"
                                    )
                                    from src.adapters.litellm_adapter import (
                                        LiteLLMAdapter,
                                    )

                                    self.adapters[name] = LiteLLMAdapter(
                                        model_name=f"openai/{name}",
                                        api_key=custom_key,
                                        base_url=base,
                                    )
                                    if name in settings.disabled_models:
                                        continue
                                    (
                                        p_cost,
                                        c_cost,
                                        supports_function_calling,
                                        max_input_tokens,
                                        max_tokens,
                                    ) = pricing_manager.get_model_pricing(name)
                                    if p_cost is None:
                                        p_cost, c_cost = 0.0, 0.0
                                    supports_function_calling = True
                                    cost = (p_cost + c_cost) / 2
                                    self.add_model(
                                        name,
                                        name,
                                        cost,
                                        1.0,
                                        0.8,
                                        None,
                                        p_cost,
                                        c_cost,
                                        supports_function_calling,
                                        max_input_tokens,
                                        max_tokens,
                                    )
                                    self.load_balancer.add_model(name, name, 1.0)
                    else:
                        resp = httpx.get(
                            f"{custom_url}/models",
                            headers={"Authorization": f"Bearer {custom_key}"},
                            timeout=3,
                        )
                        if resp.status_code == 200:
                            for m in resp.json().get("data", []):
                                name = m.get("id")
                                if (
                                    name
                                    and name not in self.models
                                    and (
                                        name not in settings.disabled_models
                                        or name == sentiment_model_id
                                    )
                                ):
                                    if name not in self.adapters:
                                        base = (
                                            custom_url
                                            if custom_url.endswith("/v1")
                                            else f"{custom_url}/v1"
                                        )
                                        from src.adapters.litellm_adapter import (
                                            LiteLLMAdapter,
                                        )

                                        self.adapters[name] = LiteLLMAdapter(
                                            model_name=f"openai/{name}",
                                            api_key=custom_key,
                                            base_url=base,
                                        )
                                    if name in settings.disabled_models:
                                        continue
                                    (
                                        p_cost,
                                        c_cost,
                                        supports_function_calling,
                                        max_input_tokens,
                                        max_tokens,
                                    ) = pricing_manager.get_model_pricing(name)
                                    if p_cost is None:
                                        p_cost, c_cost = 0.001, 0.001
                                    supports_function_calling = True
                                    cost = (p_cost + c_cost) / 2
                                    self.add_model(
                                        name,
                                        name,
                                        cost,
                                        1.0,
                                        0.8,
                                        None,
                                        p_cost,
                                        c_cost,
                                        supports_function_calling,
                                        max_input_tokens,
                                        max_tokens,
                                    )
                                    self.load_balancer.add_model(name, name, 1.0)
                except Exception as e:
                    logger.warning(
                        f"Auto-discovery failed for custom URL {custom_url}: {e}"
                    )

            # 2. OpenAI
            openai_key = settings.openai_api_key
            if openai_key and openai_key != "dummy":
                try:
                    resp = httpx.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        timeout=3,
                    )
                    if resp.status_code == 200:
                        for m in resp.json().get("data", []):
                            name = m.get("id")
                            if name and (
                                "gpt" in name
                                or "o1" in name
                                or name == sentiment_model_id
                            ):
                                if name not in self.models and (
                                    name not in settings.disabled_models
                                    or name == sentiment_model_id
                                ):
                                    if name not in self.adapters:
                                        from src.adapters.litellm_adapter import (
                                            LiteLLMAdapter,
                                        )

                                        self.adapters[name] = LiteLLMAdapter(
                                            model_name=name, api_key=openai_key
                                        )
                                    if name in settings.disabled_models:
                                        continue
                                    (
                                        p_cost,
                                        c_cost,
                                        supports_function_calling,
                                        max_input_tokens,
                                        max_tokens,
                                    ) = pricing_manager.get_model_pricing(name)
                                    if p_cost is None:
                                        if "gpt-4o-mini" in name:
                                            p_cost, c_cost = 0.00015, 0.0006
                                        elif "gpt-4o" in name:
                                            p_cost, c_cost = 0.0025, 0.005
                                        elif "gpt-4" in name:
                                            p_cost, c_cost = 0.03, 0.06
                                        elif "gpt-3.5" in name:
                                            p_cost, c_cost = 0.0005, 0.0015
                                        else:
                                            p_cost, c_cost = 0.002, 0.002
                                    supports_function_calling = True
                                    cost = (p_cost + c_cost) / 2
                                    self.add_model(
                                        name,
                                        name,
                                        cost,
                                        0.5,
                                        0.9,
                                        None,
                                        p_cost,
                                        c_cost,
                                        supports_function_calling,
                                        max_input_tokens,
                                        max_tokens,
                                    )
                                    self.load_balancer.add_model(name, name, 1.0)
                except Exception as e:
                    logger.warning(f"Auto-discovery failed for OpenAI: {e}")

            # 3. Gemini
            gemini_key = settings.gemini_api_key
            if gemini_key and gemini_key != "dummy":
                try:
                    # Merge both compat and native lists to catch missing bleeding edge models
                    gemini_discovered = []

                    # A. Compat API
                    resp1 = httpx.get(
                        "https://generativelanguage.googleapis.com/v1beta/openai/models",
                        headers={"Authorization": f"Bearer {gemini_key}"},
                        timeout=3,
                    )
                    if resp1.status_code == 200:
                        for m in resp1.json().get("data", []):
                            name = m.get("id")
                            if name and name.startswith("models/"):
                                name = name[7:]
                            if (
                                name
                                and ("gemini" in name or "gemma" in name)
                                and "embedding" not in name
                            ):
                                if name not in gemini_discovered:
                                    gemini_discovered.append(name)

                    # B. Native API
                    resp2 = httpx.get(
                        f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}",
                        timeout=3,
                    )
                    if resp2.status_code == 200:
                        for m in resp2.json().get("models", []):
                            name = m.get("name")
                            if name and name.startswith("models/"):
                                name = name[7:]
                            if (
                                name
                                and ("gemini" in name or "gemma" in name)
                                and "embedding" not in name
                            ):
                                if name not in gemini_discovered:
                                    gemini_discovered.append(name)

                    for name in gemini_discovered:
                        if name not in self.models and (
                            name not in settings.disabled_models
                            or name == sentiment_model_id
                        ):
                            if name not in self.adapters:
                                from src.adapters.litellm_adapter import LiteLLMAdapter

                                self.adapters[name] = LiteLLMAdapter(
                                    model_name=f"gemini/{name}", api_key=gemini_key
                                )
                            if name in settings.disabled_models:
                                continue
                            (
                                p_cost,
                                c_cost,
                                supports_function_calling,
                                max_input_tokens,
                                max_tokens,
                            ) = pricing_manager.get_model_pricing(name)
                            if p_cost is None:
                                if "flash" in name:
                                    p_cost, c_cost = 0.000075, 0.0003
                                elif "pro" in name:
                                    p_cost, c_cost = 0.00125, 0.005
                                else:
                                    p_cost, c_cost = 0.00035, 0.00035
                            supports_function_calling = True
                            cost = (p_cost + c_cost) / 2
                            self.add_model(
                                name,
                                name,
                                cost,
                                0.4,
                                0.88,
                                None,
                                p_cost,
                                c_cost,
                                supports_function_calling,
                                max_input_tokens,
                                max_tokens,
                            )
                            self.load_balancer.add_model(name, name, 1.0)
                except Exception as e:
                    logger.warning(f"Auto-discovery failed for Gemini: {e}")

            logger.info(f"Total configured and discovered models: {len(self.models)}")

            # Ensure sentiment model adapter is loaded even if the model is disabled for routing
            if sentiment_model_id and sentiment_model_id not in self.adapters:
                logger.info(
                    f"Force-loading adapter for sentiment model: {sentiment_model_id}"
                )
                # We need to find the credentials for this model.
                # It could be in the static config or be a discoverable model.
                config_models = load_models_from_config()
                model_info = config_models.get(sentiment_model_id)

                api_key = None
                base_url = None
                model_name_for_adapter = sentiment_model_id

                if model_info:
                    api_key = model_info.get("api_key")
                    base_url = model_info.get("base_url")
                    model_name_for_adapter = model_info.get("model", sentiment_model_id)
                else:
                    # Attempt to infer from provider-specific env vars if not in static config
                    if "gpt" in sentiment_model_id:
                        api_key = settings.openai_api_key
                    elif "gemini" in sentiment_model_id:
                        api_key = settings.gemini_api_key
                        model_name_for_adapter = f"gemini/{sentiment_model_id}"
                    # Add other providers as necessary

                if api_key:
                    from src.adapters.litellm_adapter import LiteLLMAdapter

                    self.adapters[sentiment_model_id] = LiteLLMAdapter(
                        model_name=model_name_for_adapter,
                        api_key=api_key,
                        base_url=base_url,
                    )
                    logger.info(
                        f"Successfully loaded adapter for sentiment model: {sentiment_model_id}"
                    )
                else:
                    logger.warning(
                        f"Could not find credentials to load sentiment model: {sentiment_model_id}"
                    )

        except Exception as e:
            logger.error(f"Error loading models: {str(e)}")

    def add_model(
        self,
        model_id: str,
        model_name: str,
        cost: float,
        time: float,
        probability: float,
        concurrency_limit: Optional[int] = None,
        prompt_cost: Optional[float] = None,
        completion_cost: Optional[float] = None,
        supports_function_calling: bool = False,
        max_input_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ):
        """Add a model to the router"""
        self.models[model_id] = {
            "name": model_name,
            "cost": cost,
            "prompt_cost": prompt_cost if prompt_cost is not None else cost,
            "completion_cost": completion_cost if completion_cost is not None else cost,
            "time": time,
            "probability": probability,
            "concurrency_limit": concurrency_limit,
            "supports_function_calling": supports_function_calling,
            "max_input_tokens": max_input_tokens,
            "max_tokens": max_tokens,
        }
        self.metrics[model_id] = {
            "requests": 0,
            "total_cost": 0.0,
            "total_time": 0.0,
            "success_rate": 0.0,
        }
        logger.info(f"Added model {model_name} ({model_id}) to router")

    def _get_semaphore(self, model_id: str) -> Optional[asyncio.Semaphore]:
        """Get or create an asyncio Semaphore for the given model_id based on configuration"""
        if model_id not in self.concurrency_limits:
            limit = self.models.get(model_id, {}).get("concurrency_limit")
            if limit and limit > 0:
                self.concurrency_limits[model_id] = asyncio.Semaphore(limit)
                logger.info(f"Created semaphore for {model_id} with limit {limit}")
            else:
                return None
        return self.concurrency_limits.get(model_id)

    def extract_coding_features(
        self,
        request: RoutingRequest,
        model_id: str,
        response: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Extract agent features for Reality Check™ calibration"""
        query = request.query or ""
        agent_id = request.agent_id or "default"
        features = {
            "model_id": "unknown",
            "agent_id": "default",
            "model_provider": "unknown",
            "struct_nodes": 0.0,
            "struct_height": 0.0,
            "struct_loop_dens": 0.0,
            "struct_logic_dens": 0.0,
            "struct_func_dens": 0.0,
            "struct_dep_count": 0.0,
            "trace_iter_idx": 0.0,
            "trace_iter_ratio": 0.0,
            "trace_err_flag": 0.0,
            "trace_read_freq": 0.0,
            "trace_write_freq": 0.0,
            "trace_exec_freq": 0.0,
            "trace_search_freq": 0.0,
            "meta_pos_con": 0.0,
            "meta_neg_con": 0.0,
            "meta_id_dens": 0.0,
            "meta_grounding": 0.0,
            "sem_gen": 0.0,
            "sem_fix": 0.0,
            "sem_refactor": 0.0,
            "sem_docs": 0.0,
            "tele_p_len": float(len(query)),
            "tele_hist_depth": 0.0,
            "tele_ctx_pressure": 0.0,
            "tele_avg_msg": 0.0,
            "confidence": 0.0,
            "entropy": 0.0,
            "logprobs_mean": 0.0,
            "logprobs_std": 0.0,
            "first_token_logprob": 0.0,
            "second_token_logprob": 0.0,
            "is_empty": 0.0,
            "is_truncated": 0.0,
            "is_malformed": 0.0,
            "is_lazy": 0.0,
            "is_refusal": 0.0,
        }

        # Categorical Features
        features["model_id"] = model_id
        features["agent_id"] = agent_id

        # Model Capabilities
        m_info = self.models.get(model_id, {})
        features["model_supports_tools"] = (
            1.0 if m_info.get("supports_function_calling") else 0.0
        )
        features["model_context_window"] = float(m_info.get("max_input_tokens") or 8192)
        features["model_max_output"] = float(m_info.get("max_tokens") or 4096)

        # Pricing/Tier Indicators (Cost correlates with reasoning power)
        features["model_prompt_cost"] = float(
            m_info.get("prompt_cost", m_info.get("cost", 0.0))
        )
        features["model_completion_cost"] = float(
            m_info.get("completion_cost", m_info.get("cost", 0.0))
        )

        # Additional probed capabilities
        features["model_supports_logprobs"] = (
            1.0 if m_info.get("supports_logprobs") else 0.0
        )

        # Provider and Locality
        is_local = 0.0
        provider = "unknown"
        adapter = self.adapters.get(model_id)
        if adapter:
            adapter_name = type(adapter).__name__
            if adapter_name == "LiteLLMAdapter":
                provider_str = getattr(adapter, "model_name", "")
                base_url = getattr(adapter, "base_url", "") or ""
                if (
                    "11434" in base_url
                    or "localhost" in base_url
                    or "127.0.0.1" in base_url
                ):
                    is_local = 1.0
                    provider = "ollama"
                elif provider_str.startswith("openai/"):
                    provider = "openai"
                elif provider_str.startswith("anthropic/"):
                    provider = "anthropic"
                elif provider_str.startswith("gemini/"):
                    provider = "gemini"
                elif provider_str.startswith("cohere/"):
                    provider = "cohere"
                elif provider_str.startswith("ollama/"):
                    provider = "ollama"
                    is_local = 1.0
                else:
                    provider = "custom_litellm"

        features["model_is_local"] = is_local
        features["model_provider"] = provider

        # Structural analysis if code
        try:
            tree = ast.parse(query)
            nodes = list(ast.walk(tree))
            features["struct_nodes"] = float(len(nodes))

            def get_h(node):
                ch = list(ast.iter_child_nodes(node))
                return 1 + max([get_h(c) for c in ch], default=0)

            features["struct_height"] = float(get_h(tree))
            features["struct_loop_dens"] = len(
                [n for n in nodes if isinstance(n, (ast.For, ast.While))]
            ) / max(1, len(nodes))
            features["struct_logic_dens"] = len(
                [n for n in nodes if isinstance(n, (ast.If, ast.BoolOp, ast.Compare))]
            ) / max(1, len(nodes))
            features["struct_func_dens"] = len(
                [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
            ) / max(1, len(nodes))
            features["struct_dep_count"] = float(
                len(re.findall(r"^(import|from)\s", query, re.M))
            )
        except:
            pass

        # Trace analysis from messages
        params = request.parameters or {}
        msgs = params.get("messages", [])
        it = params.get("iteration", 1)
        mx = params.get("max_iterations", 10)
        features["trace_iter_idx"] = float(it)
        features["trace_iter_ratio"] = it / mx if mx > 0 else 0.0
        features["tele_hist_depth"] = float(len(msgs))
        for m in msgs:
            content = str(m.get("content", "")).lower()
            if "error" in content or "exception" in content:
                features["trace_err_flag"] = 1.0
            if "read" in content or "file" in content:
                features["trace_read_freq"] += 1.0
            if "write" in content or "save" in content:
                features["trace_write_freq"] += 1.0
            if "execute" in content or "run" in content:
                features["trace_exec_freq"] += 1.0
            if "search" in content or "find" in content:
                features["trace_search_freq"] += 1.0

        for k in [
            "trace_read_freq",
            "trace_write_freq",
            "trace_exec_freq",
            "trace_search_freq",
        ]:
            if len(msgs) > 0:
                features[k] /= float(len(msgs))

        # Metadata
        q_low = query.lower()
        features["meta_pos_con"] = float(
            len(re.findall(r"\b(must|use|always)\b", q_low))
        )
        features["meta_neg_con"] = float(
            len(re.findall(r"\b(don't|never|avoid)\b", q_low))
        )
        ids = re.findall(r"[a-zA-Z_]\w*", query)
        features["meta_id_dens"] = (
            len(ids) / len(query.split()) if query.split() else 0.0
        )
        features["meta_grounding"] = (
            1.0 if re.search(r"(/|\\|\.\w{2,4})", query) else 0.0
        )

        # Semantic Intent
        if any(w in q_low for w in ["create", "write", "generate", "implement"]):
            features["sem_gen"] = 1.0
        if any(w in q_low for w in ["fix", "bug", "debug", "error"]):
            features["sem_fix"] = 1.0
        if any(w in q_low for w in ["refactor", "optimize", "clean"]):
            features["sem_refactor"] = 1.0
        if any(w in q_low for w in ["document", "comment", "readme"]):
            features["sem_docs"] = 1.0

        # Telemetry
        features["tele_ctx_pressure"] = min(1.0, len(str(msgs)) / 128000)
        features["tele_avg_msg"] = (
            statistics.mean([len(str(m.get("content", ""))) for m in msgs])
            if msgs
            else 0.0
        )

        # Query Signature Hash
        q_hs = hashlib.md5(query.encode()).hexdigest()
        for i in range(min(6, len(q_hs) // 2)):
            features[f"query_hash_{i}"] = int(q_hs[i * 2 : i * 2 + 2], 16) / 255.0

        # Post-response metrics
        if response:
            features["confidence"] = response.get("confidence", 0.0)
            features["entropy"] = response.get("entropy", 0.0)
            features["logprobs_mean"] = response.get("logprobs_mean", 0.0)
            features["logprobs_std"] = response.get("logprobs_std", 0.0)
            features["first_token_logprob"] = response.get("first_token_logprob", 0.0)
            features["second_token_logprob"] = response.get("second_token_logprob", 0.0)
            features["is_empty"] = 1.0 if response.get("is_empty") else 0.0
            features["is_truncated"] = 1.0 if response.get("is_truncated") else 0.0
            features["is_malformed"] = 1.0 if response.get("is_malformed") else 0.0
            features["is_lazy"] = 1.0 if response.get("is_lazy") else 0.0
            features["is_refusal"] = 1.0 if response.get("is_refusal") else 0.0

        return features

    def _estimate_tokens(self, request: RoutingRequest) -> int:
        """Estimate the number of tokens in the request."""
        total_chars = 0
        if request.query:
            total_chars += len(request.query)

        if request.parameters and "messages" in request.parameters:
            for msg in request.parameters["messages"]:
                content = msg.get("content", "")
                if isinstance(content, str):
                    total_chars += len(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            total_chars += len(item.get("text", ""))

        return total_chars // 4

    async def get_ranked_models(
        self, request: RoutingRequest, strategy: str = "expected_utility"
    ) -> List[RoutingDecision]:
        """Select models and rank them using Reality Check calibration"""
        logger.debug(f"Ranking models using strategy: {strategy}")
        if not self.models:
            raise HTTPException(
                status_code=500, detail="No models available for routing"
            )

        db = SessionLocal()
        try:
            # Estimate prompt tokens (roughly 4 chars per token)
            total_estimated_tokens = self._estimate_tokens(request)

            if strategy == "load_balanced":
                model_id = self.load_balancer.get_next_model("weighted", db)
                if model_id is None:
                    raise HTTPException(
                        status_code=500, detail="No suitable model found"
                    )
                m = self.models[model_id]
                p_cost = m.get("prompt_cost", m["cost"])
                c_cost = m.get("completion_cost", m["cost"])
                estimated_cost = (total_estimated_tokens * p_cost / 1000.0) + (
                    500 * c_cost / 1000.0
                )
                return [
                    RoutingDecision(
                        model_id=model_id,
                        expected_utility=0.0,
                        cost=estimated_cost,
                        time=m["time"],
                        probability=m["probability"],
                        name=m["name"],
                    )
                ]

            tools_requested = bool(
                request.parameters and request.parameters.get("tools")
            )

            model_tasks = []
            for mid, info in self.models.items():
                # If tools are requested, only include models that strictly support them
                if tools_requested and not info.get("supports_function_calling", False):
                    continue

                if (
                    info.get("max_input_tokens")
                    and total_estimated_tokens > info["max_input_tokens"]
                ):
                    continue
                # Do NOT compare input tokens against max_tokens (output limit)
                # Only check max_input_tokens
                # if (
                #     info.get("max_tokens")
                #     and total_estimated_tokens > info["max_tokens"]
                # ):
                #     continue
                recent = (
                    db.query(RoutingLog)
                    .filter(RoutingLog.model_id == mid, RoutingLog.time != None)
                    .order_by(RoutingLog.timestamp.desc())
                    .limit(20)
                    .all()
                )
                time_val = (
                    statistics.mean([l.time for l in recent])
                    if recent
                    else info["time"]
                )

                p_cost = info.get("prompt_cost", info["cost"])
                c_cost = info.get("completion_cost", info["cost"])
                avg_completion = (
                    statistics.mean(
                        [l.completion_tokens for l in recent if l.completion_tokens]
                    )
                    if recent and any(l.completion_tokens for l in recent)
                    else 500
                )
                estimated_cost = (total_estimated_tokens * p_cost / 1000.0) + (
                    avg_completion * c_cost / 1000.0
                )

                features = self.extract_coding_features(request, mid)
                model_tasks.append(
                    {
                        "id": mid,
                        "name": info["name"],
                        "cost": estimated_cost,
                        "time": time_val,
                        "features": features,
                    }
                )

            async def call_rc(m):
                try:
                    async with httpx.AsyncClient() as client:
                        # Strategy 1 (Reality Routing) uses the realityrouter endpoint
                        url = (
                            REALITY_ROUTING_URL
                            if strategy == "expected_utility"
                            else REALITY_REROUTING_URL
                        )
                        resp = await client.post(
                            f"{url}/decide",
                            json={"features": m["features"]},
                            headers={"x-api-key": get_reality_check_key(url)},
                            timeout=5.0,
                        )
                        if resp.status_code == 200:
                            r = resp.json()
                            # Support multiple possible keys for probability and uncertainty
                            # Support multiple possible keys for probability and uncertainty from Reality Check API
                            prob = r.get("prob_true")
                            if prob is None:
                                prob = r.get("probability")
                            if prob is None:
                                prob = r.get("p")
                            if prob is None:
                                prob = r.get("prob")
                            if prob is None:
                                prob = 0.5

                            uncertainty = r.get("uncertainty", 0.0)

                            logger.debug(
                                f"Reality Check calibration for {m['id']}: prob={prob:.4f}, uncert={uncertainty:.4f}, id={r.get('decision_id')}"
                            )
                            return {
                                **m,
                                "prob": prob,
                                "uncertainty": uncertainty,
                                "rc_id": r.get("decision_id"),
                                "fb_req": r.get("feedback_requested", False),
                            }
                        else:
                            error_body = resp.text
                            logger.warning(
                                f"Reality Check API returned {resp.status_code} for {m['id']}: {error_body}"
                            )
                except Exception as e:
                    logger.error(f"Reality Check call failed for {m['id']}: {e}")

                return {
                    **m,
                    "prob": 0.5,
                    "uncertainty": 0.5,
                    "rc_id": None,
                    "fb_req": False,
                }

            results = await asyncio.gather(*[call_rc(m) for m in model_tasks])

            decisions = []
            feedback_candidates = []
            for r in results:
                utility = self.utility_calculator.calculate_expected_utility(
                    r["cost"], r["time"], r["prob"]
                )
                is_zed = request.agent_id and "zed" in request.agent_id.lower()
                if (
                    is_zed
                    and "claude-3" in r["id"].lower()
                    and "sonnet" in r["id"].lower()
                ):
                    utility *= 1.20

                d = RoutingDecision(
                    model_id=r["id"],
                    expected_utility=utility,
                    cost=r["cost"],
                    time=r["time"],
                    probability=r["prob"],
                    uncertainty=r.get("uncertainty", 0.0),
                    name=r["name"],
                    reality_check_id=r["rc_id"],
                    feedback_required=r["fb_req"],
                )
                decisions.append(d)
                if r["fb_req"]:
                    feedback_candidates.append(d)

            import random

            # Filter out circuit-tripped models before final ranking
            original_decisions = list(decisions)
            decisions = [
                d for d in decisions if self.load_balancer.is_model_healthy(d.model_id)
            ]

            if not decisions:
                # If all healthy models are gone, fallback to full list to avoid empty routing
                decisions = original_decisions

            # Also ensure feedback candidates only include healthy models
            feedback_candidates = [
                d
                for d in feedback_candidates
                if self.load_balancer.is_model_healthy(d.model_id)
            ]

            if strategy == "tiered_assessment":
                # Start with cheapest models first for tiered escalation
                decisions.sort(key=lambda x: (x.cost, x.time))
            else:
                # Group by utility to break ties randomly
                utility_groups = {}
                for d in decisions:
                    rounded_utility = round(d.expected_utility, 4)
                    if rounded_utility not in utility_groups:
                        utility_groups[rounded_utility] = []
                    utility_groups[rounded_utility].append(d)

                shuffled_decisions = []
                for util in sorted(utility_groups.keys(), reverse=True):
                    group = utility_groups[util]
                    if len(group) > 1:
                        random.shuffle(group)
                        for d in group:
                            d.is_random_exploration = True
                    shuffled_decisions.extend(group)
                decisions = shuffled_decisions

            if feedback_candidates and strategy != "tiered_assessment":
                winner = random.choice(feedback_candidates)
                for i, d in enumerate(decisions):
                    if d.model_id == winner.model_id:
                        decisions.insert(0, decisions.pop(i))
                        break

            return decisions
        finally:
            db.close()

    def log_routing_decision(
        self,
        decision: RoutingDecision,
        request: RoutingRequest,
        response: Dict[str, Any],
        db: Session,
        routing_context: str = None,
        features: Dict[str, Any] = None,
        user_sentiment: str = None,
    ):
        try:
            prompt_tokens = (
                response.get("usage", {}).get("prompt_tokens", 0)
                if response.get("usage")
                else 0
            )
            completion_tokens = (
                response.get("usage", {}).get("completion_tokens", 0)
                if response.get("usage")
                else 0
            )
            total_tokens = (
                response.get("usage", {}).get("total_tokens", 0)
                if response.get("usage")
                else 0
            )

            # Fallback estimation for missing completion tokens
            if (
                completion_tokens == 0
                and response
                and (response.get("text") or response.get("tool_calls"))
            ):
                text_len = len(response.get("text") or "")
                tool_calls_len = len(json.dumps(response.get("tool_calls") or []))
                completion_tokens = max(1, int((text_len + tool_calls_len) / 4))
                if prompt_tokens > 0:
                    total_tokens = prompt_tokens + completion_tokens

            req_payload = json.dumps(request.parameters) if request.parameters else "{}"
            resp_payload = json.dumps(response) if response else "{}"
            is_success = bool(response)

            actual_cost = decision.cost
            if decision.model_id in self.models:
                m_info = self.models[decision.model_id]
                p_cost = m_info.get("prompt_cost", m_info["cost"])
                c_cost = m_info.get("completion_cost", m_info["cost"])
                actual_cost = (prompt_tokens * p_cost / 1000.0) + (
                    completion_tokens * c_cost / 1000.0
                )
            elif prompt_tokens > 0 or completion_tokens > 0:
                actual_cost = (total_tokens * decision.cost) / 1000.0

            potential_max_cost = actual_cost
            if self.models and (prompt_tokens > 0 or completion_tokens > 0):
                for model_config in self.models.values():
                    p_c = model_config.get("prompt_cost", model_config.get("cost", 0.0))
                    c_c = model_config.get(
                        "completion_cost", model_config.get("cost", 0.0)
                    )
                    if p_c is None:
                        p_c = model_config.get("cost", 0.0)
                    if c_c is None:
                        c_c = model_config.get("cost", 0.0)
                    m_potential = (prompt_tokens * p_c / 1000.0) + (
                        completion_tokens * c_c / 1000.0
                    )
                    if m_potential > potential_max_cost:
                        potential_max_cost = m_potential

            # Determine the strategy for metrics logging
            log_strategy = (
                request.parameters.get("strategy") if request.parameters else None
            )
            if not log_strategy:
                # Fallback to local default if not in request
                log_strategy = get_settings().default_strategy

            metrics_collector.collect_routing_metrics(
                db=db,
                model_id=decision.model_id,
                model_name=decision.name,
                expected_utility=decision.expected_utility,
                cost=actual_cost,
                time=decision.time,
                probability=decision.probability,
                success=is_success,
                query=request.query,
                strategy=log_strategy,
                agent_id=request.agent_id,
                response_text=response.get("text", ""),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                request_payload=req_payload,
                response_payload=resp_payload,
                routing_context=routing_context,
                confidence=response.get("confidence"),
                entropy=response.get("entropy"),
                logprobs_mean=response.get("logprobs_mean"),
                logprobs_std=response.get("logprobs_std"),
                first_token_logprob=response.get("first_token_logprob"),
                first_token_top_logprobs=json.dumps(
                    response.get("first_token_top_logprobs")
                )
                if response.get("first_token_top_logprobs")
                else None,
                second_token_logprob=response.get("second_token_logprob"),
                second_token_top_logprobs=json.dumps(
                    response.get("second_token_top_logprobs")
                )
                if response.get("second_token_top_logprobs")
                else None,
            )

            # Retrieve the log entry we just created to update extra fields
            log_entry = (
                db.query(RoutingLog)
                .filter(RoutingLog.model_id == decision.model_id)
                .filter(RoutingLog.query == request.query)
                .filter(RoutingLog.agent_id == (request.agent_id or "default"))
                .order_by(RoutingLog.timestamp.desc())
                .first()
            )

            if log_entry:
                if features:
                    log_entry.features_json = json.dumps(features)
                if user_sentiment:
                    log_entry.user_sentiment = user_sentiment
                if decision.reality_check_id:
                    log_entry.reality_check_id = str(decision.reality_check_id)
                log_entry.potential_cost = potential_max_cost
                db.commit()
                logger.debug(
                    f"Updated log entry {log_entry.id} with RC ID and features"
                )

            logger.debug(f"Logged routing decision for model {decision.model_id}")
        except Exception as e:
            logger.error(f"Error logging routing decision: {str(e)}")

    async def assess_user_sentiment(self, request: RoutingRequest) -> Optional[str]:
        """Assess user sentiment based on conversation history for feedback"""
        try:
            messages = (request.parameters or {}).get("messages", [])
            # Limit context to last 5 messages to avoid blowing up context window and cost
            messages = messages[-5:]

            logger.debug(
                f"Assessing sentiment for interaction with {len(messages)} messages. Agent: {request.agent_id}"
            )
            if len(messages) < 2:
                return None

            def _get_text(msg):
                content = msg.get("content", "")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list) and content:
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            break
                    if not text:
                        text = str(content[0].get("text", ""))

                # If there's no textual content, check if it's a tool call and summarize it
                if not text:
                    tool_calls = msg.get("tool_calls", [])
                    if tool_calls:
                        tool_names = [
                            tc.get("function", {}).get("name", "unknown")
                            for tc in tool_calls
                        ]
                        text = f"[Invoked Tools: {', '.join(tool_names)}]"
                return text.strip()

            # Filter messages to find those with actual content
            contentful_messages = []
            for msg in messages:
                role = msg.get("role", "")
                text = _get_text(msg)
                if text:
                    # We store a cleaned up version of the message for sentiment context
                    contentful_messages.append(
                        {"role": role, "content": text, "raw_msg": msg}
                    )

            if len(contentful_messages) < 2:
                logger.debug("Not enough contentful messages for sentiment assessment")
                return None

            # Get the most recent contentful message
            curr_msg_data = contentful_messages[-1]
            curr_role = curr_msg_data["role"]
            curr_content = curr_msg_data["content"]

            # Get the previous contentful message
            prev_msg_data = contentful_messages[-2]
            prev_role = prev_msg_data["role"]
            prev_content = prev_msg_data["content"]

            # Check for the standard user follow-up pattern first (OLD STYLE)
            # This allows backward compatibility with existing systems
            if prev_role == "assistant" and curr_role == "user":
                # Standard user follow-up to assistant response - OLD STYLE
                prompt = (
                    f"Analyze the following interaction to see if the user was satisfied with the assistant's last response.\n\n"
                    f'Assistant\'s Response: "{prev_content}"\n'
                    f'User\'s Follow-up: "{curr_content}"\n\n'
                    f"Determine if the user's follow-up indicates they were 'happy' with the previous answer, "
                    f"'unhappy' (e.g., they corrected it, complained, or asked for a redo), or if it's 'indeterminate'.\n"
                    f"Respond with EXACTLY one word: happy, unhappy, or indeterminate."
                )
                logger.info(
                    "Analyzing user follow-up to assistant response (old style)"
                )
            else:
                # NEW GENERAL STYLE: Any message response to previous message
                # This includes scenarios like assistant responding to user's initial query,
                # or user responding to an agent's query, to detect sentiment on the last interaction
                prompt = (
                    f"Analyze if a message indicates positive (happy), negative (unhappy), or indeterminate sentiment."
                    f"Consider the following messages and evaluate the sentiment of the final message with respect to the previous one:\n\n"
                    f'Previous Message (from {prev_role}): "{prev_content}"\n'
                    f'Current Message (from {curr_role}): "{curr_content}"\n\n'
                    f"Determine the overall sentiment of the final message with respect to the previous message.\n"
                    f"Respond with EXACTLY one word: happy, unhappy, or indeterminate."
                )
                logger.debug(
                    "Analyzing sentiment of message with respect to previous one (new style)"
                )

            # Use the configured sentiment model, or fall back to the cheapest available model
            settings = get_settings()
            sentiment_id = settings.sentiment_model_id

            if not sentiment_id or sentiment_id not in self.adapters:
                # Fallback to the cheapest model, assuming it's capable enough for sentiment
                sentiment_id = min(
                    self.models.keys(), key=lambda m: self.models[m].get("cost", 0)
                )
            adapter = self.adapters.get(sentiment_id)
            if not adapter:
                return None

            resp = await adapter.forward_request(
                RoutingRequest(
                    query="",
                    parameters={
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 256,
                        "temperature": 0,
                    },
                )
            )

            raw_txt = str(resp.get("text", "")).lower().strip()
            txt = raw_txt.replace("'", "").replace('"', "").replace("`", "").strip()

            # Improved keyword matching to avoid false positives in reasoning
            # Ensure we match the word correctly even if it's in a sentence
            if re.search(r"\bunhappy\b", txt):
                sentiment = "unhappy"
            elif re.search(r"\bhappy\b", txt):
                sentiment = "happy"
            else:
                sentiment = "indeterminate"

            logger.debug(f"Assessed user sentiment: {sentiment}")
            return sentiment
        except Exception as e:
            logger.error(f"Sentiment assessment failed: {e}")
            return "indeterminate"

    async def route_request(
        self, request: RoutingRequest, strategy: Optional[str] = None
    ) -> RoutingResponse:
        settings = get_settings()
        if strategy is None:
            strategy = settings.default_strategy

        logger.debug(f"Routing request with strategy: {strategy}")
        db = SessionLocal()
        try:
            # Strip fragile thought signatures from incoming messages to prevent Google 400 errors
            if request.parameters and "messages" in request.parameters:
                for msg in request.parameters["messages"]:
                    if isinstance(msg, dict):
                        if msg.get("tool_calls"):
                            for tc in msg["tool_calls"]:
                                if (
                                    isinstance(tc, dict)
                                    and "id" in tc
                                    and isinstance(tc["id"], str)
                                    and "__thought__" in tc["id"]
                                ):
                                    tc["id"] = tc["id"].split("__thought__")[0]
                        if (
                            msg.get("tool_call_id")
                            and isinstance(msg["tool_call_id"], str)
                            and "__thought__" in msg["tool_call_id"]
                        ):
                            msg["tool_call_id"] = msg["tool_call_id"].split(
                                "__thought__"
                            )[0]

            # Check for sticky session
            if (
                request.agent_id
                and request.parameters
                and "messages" in request.parameters
                and request.parameters["messages"]
            ):
                import hashlib
                import json

                from fastapi import HTTPException

                first_msg_str = json.dumps(
                    request.parameters["messages"][0], sort_keys=True
                )
                session_str = f"{request.agent_id}_{first_msg_str}"
                session_hash = hashlib.sha256(session_str.encode("utf-8")).hexdigest()
                session_id = f"zed_{session_hash}"

                if (
                    getattr(self, "active_sessions", None) is not None
                    and session_id in self.active_sessions
                ):
                    model_id = self.active_sessions[session_id]
                    if model_id not in self.models:
                        raise HTTPException(
                            status_code=503, detail="Sticky model unavailable"
                        )

                    adapter = self.adapters.get(model_id)
                    if adapter:
                        response = await adapter.forward_request(request)
                        if (
                            response
                            and isinstance(response, dict)
                            and response.get("tool_calls")
                        ):
                            for tc in response["tool_calls"]:
                                if (
                                    isinstance(tc, dict)
                                    and "id" in tc
                                    and isinstance(tc["id"], str)
                                    and "__thought__" in tc["id"]
                                ):
                                    tc["id"] = tc["id"].split("__thought__")[0]
                        model_info = self.models[model_id]
                        actual_cost = (
                            response.get("cost", model_info.get("cost", 0.0))
                            if isinstance(response, dict) and response.get("cost")
                            else model_info.get("cost", 0.0)
                        )
                        return RoutingResponse(
                            model_id=model_id,
                            model_name=model_info.get("name", model_id),
                            expected_utility=10.0,
                            cost=actual_cost,
                            time=model_info.get("time", 0.0),
                            probability=model_info.get("probability", 1.0),
                            response=response,
                            decision_log={},
                        )
            ranked_decisions = await self.get_ranked_models(request, strategy)
            sentiment = await self.assess_user_sentiment(request)

            if sentiment in ["happy", "unhappy"]:
                try:
                    last_log = (
                        db.query(RoutingLog)
                        .filter(RoutingLog.agent_id == request.agent_id)
                        .filter(RoutingLog.reality_check_id.isnot(None))
                        .filter(RoutingLog.user_sentiment.is_(None))
                        .order_by(RoutingLog.timestamp.desc())
                        .first()
                    )

                    if last_log:
                        last_log.user_sentiment = sentiment
                        db.commit()
                        logger.info(
                            f"Updated log entry {last_log.id} sentiment to {sentiment}"
                        )

                        try:
                            # Use string ID for feedback as expected by Reality Check API
                            rc_id_str = str(last_log.reality_check_id)

                            # Determine correct URL based on the log's original strategy
                            fb_strategy = (
                                last_log.strategy or strategy or "expected_utility"
                            )
                            url = (
                                REALITY_ROUTING_URL
                                if fb_strategy == "expected_utility"
                                else REALITY_REROUTING_URL
                            )

                            feedback_val = 1 if sentiment == "happy" else 0
                            fb_payload = {
                                "decision_id": int(rc_id_str),
                                "feedback": feedback_val,
                            }

                            logger.info(
                                f"Sending feedback to Reality Check ({fb_strategy}) for decision {rc_id_str}: {sentiment} (Payload: {fb_payload})"
                            )
                            async with httpx.AsyncClient() as client:
                                fb_resp = await client.post(
                                    f"{url}/feedback",
                                    json=fb_payload,
                                    headers={"x-api-key": get_reality_check_key(url)},
                                    timeout=3.0,
                                )
                                fb_status = fb_resp.status_code
                                fb_text = fb_resp.text
                                logger.info(
                                    f"Reality Check feedback response: {fb_status}"
                                )
                                if fb_status != 200:
                                    logger.warning(f"Feedback error detail: {fb_text}")
                        except ValueError:
                            logger.error(
                                f"Invalid reality_check_id format: {last_log.reality_check_id}"
                            )
                except Exception as e:
                    logger.error(f"Error sending feedback to Reality Check: {e}")

            if ranked_decisions:
                title = (
                    " REALITY ROUTER: RANKING MODELS "
                    if strategy == "expected_utility"
                    else " REALITY REROUTER: RANKING MODELS "
                )
                logger.debug("=" * 116)
                logger.debug(title.center(116, "="))
                logger.debug("-" * 116)
                logger.debug(
                    f"{'  Model Name (ID)':<42} | {'Utility':>10} | {'Prob':>8} | {'Uncert':>8} | {'Cost':>8} | {'Time':>6} | {'Info':>10}"
                )
                logger.debug("-" * 116)
                for d in ranked_decisions:
                    marker = ">>" if d == ranked_decisions[0] else "  "
                    label = f"{d.name} ({d.model_id})"
                    if len(label) > 40:
                        label = label[:37] + "..."
                    info_tags = []
                    if getattr(d, "feedback_required", False):
                        info_tags.append("FB_Req")
                    elif getattr(d, "is_random_exploration", False):
                        info_tags.append("Random")
                    info_str = ",".join(info_tags)
                    logger.debug(
                        f"{marker} {label:<39} | {d.expected_utility:>10.4f} | {d.probability:>8.4f} | {d.uncertainty:>8.4f} | {d.cost:>8.4f} | {d.time:>6.2f} | {info_str:>10}"
                    )
                logger.debug("=" * 116)

            routing_context = json.dumps([d.model_dump() for d in ranked_decisions])
            last_error = None

            for decision in ranked_decisions:
                adapter = self.adapters.get(decision.model_id)
                if not adapter:
                    continue

                semaphore = self._get_semaphore(decision.model_id)

                try:
                    start_time = time.time()

                    # Create a deep copy of the request for each model attempt to avoid
                    # in-place modification leakage between models (especially for tool-stripping)
                    current_request = request.model_copy(deep=True)
                    if current_request.parameters:
                        # Strictly enforce stream=False internally so we can evaluate full response
                        current_request.parameters["stream"] = False

                    has_tools = bool(
                        current_request.parameters
                        and current_request.parameters.get("tools")
                    )
                    if has_tools:
                        # Inject a system override that forces models into standard OpenAI tool format.
                        # Most modern models (like DeepSeek) switch to raw JSON when they see
                        # tools/tool_choice parameters combined with a forceful system nudge.
                        tools_schema = current_request.parameters.get("tools")
                        format_nudge = {
                            "role": "system",
                            "content": (
                                "COMMAND: You MUST use the standard OpenAI tool-calling JSON format for all tool usage. "
                                "Do NOT use internal tags like <｜tool calls begin｜> or custom XML-like syntax. "
                                "Respond with valid tool calls using the 'tool_calls' field if available. "
                                f"Available tools:\n{json.dumps(tools_schema, indent=2)}"
                            ),
                        }

                        if (
                            current_request.parameters
                            and "messages" in current_request.parameters
                        ):
                            # Prepend nudge to message history
                            current_request.parameters["messages"] = [
                                format_nudge
                            ] + list(current_request.parameters["messages"])

                        if not self.models[decision.model_id].get(
                            "supports_function_calling"
                        ):
                            # Even if the model metadata suggests no native support, we keep tools
                            # in the request for custom/local providers (vLLM/Ollama) to handle,
                            # while relying on the system nudge for pure text models.
                            pass

                    # Check if model is healthy (circuit breaker check)
                    if not self.load_balancer.is_model_healthy(decision.model_id):
                        logger.warning(
                            f"Model {decision.model_id} is currently circuit-tripped. Skipping..."
                        )
                        continue

                    if semaphore:
                        if semaphore.locked():
                            logger.warning(
                                f"Model {decision.model_id} is at its concurrency limit. Skipping..."
                            )
                            continue

                        async with semaphore:
                            response = await adapter.forward_request(current_request)
                    else:
                        response = await adapter.forward_request(current_request)
                    elapsed_time = time.time() - start_time

                    # --- HEURISTIC TOOL RESCUE ---
                    # Some models (like Nemotron or DeepSeek) might output tool calls as JSON text
                    # in the content block instead of using the API correctly. We try to rescue these.
                    if (
                        response
                        and isinstance(response, dict)
                        and not response.get("tool_calls")
                    ):
                        resp_text = str(response.get("text", "")).strip()
                        rescued_calls = []

                        # 1. Look for DeepSeek-style tags
                        if (
                            "<｜tool call begin｜>" in resp_text
                            or "<｜tool calls begin｜>" in resp_text
                        ):
                            ds_blocks = re.findall(
                                r"<｜tool call begin｜>(.*?)<｜tool call end｜>",
                                resp_text,
                                re.DOTALL,
                            )
                            if not ds_blocks and "<｜tool call begin｜>" in resp_text:
                                ds_blocks = [
                                    resp_text.split("<｜tool call begin｜>")[-1]
                                ]

                            for i, block in enumerate(ds_blocks):
                                parts = block.split("<｜tool sep｜>")
                                t_type, t_name, t_args = "function", "", "{}"
                                if len(parts) >= 3:
                                    t_type, t_name, t_args = (
                                        parts[0].strip() or "function",
                                        parts[1].strip(),
                                        parts[2].strip(),
                                    )
                                elif len(parts) == 2:
                                    p1, p2 = parts[0].strip(), parts[1].strip()
                                    if p1 == "function":
                                        t_name = p2
                                    else:
                                        t_name, t_args = p1, p2
                                elif len(parts) == 1:
                                    t_name = parts[0].strip()

                                if t_name:
                                    rescued_calls.append(
                                        {
                                            "id": f"rescued_ds_{int(time.time())}_{i}",
                                            "type": t_type,
                                            "function": {
                                                "name": t_name,
                                                "arguments": t_args,
                                            },
                                        }
                                    )

                        # 2. Look for markdown JSON blocks
                        json_blocks = re.findall(
                            r"```json\s*\n?(.*?)\n?```", resp_text, re.DOTALL
                        )

                        # 3. Look for bare JSON objects (balanced braces)
                        if (
                            not json_blocks
                            and not rescued_calls
                            and "{" in resp_text
                            and "}" in resp_text
                        ):
                            for i in range(len(resp_text)):
                                if resp_text[i] == "{":
                                    balance = 0
                                    for j in range(i, len(resp_text)):
                                        if resp_text[j] == "{":
                                            balance += 1
                                        elif resp_text[j] == "}":
                                            balance -= 1
                                        if balance == 0:
                                            json_blocks.append(resp_text[i : j + 1])
                                            break

                        for block in json_blocks:
                            try:
                                data = json.loads(block.strip())
                                if isinstance(data, dict):
                                    # Handle various common pseudo-JSON formats
                                    name = (
                                        data.get("name")
                                        or data.get("action")
                                        or data.get("tool")
                                        or data.get("function")
                                    )
                                    args = (
                                        data.get("parameters")
                                        or data.get("arguments")
                                        or data.get("args")
                                        or data.get("input")
                                    )

                                    # Support specific "tool": "name" format
                                    if not name and "tool" in data:
                                        name = data["tool"]

                                    if name:
                                        rescued_calls.append(
                                            {
                                                "id": f"rescued_{int(time.time())}_{len(rescued_calls)}",
                                                "type": "function",
                                                "function": {
                                                    "name": str(name),
                                                    "arguments": json.dumps(args)
                                                    if isinstance(args, (dict, list))
                                                    else str(args or "{}"),
                                                },
                                            }
                                        )
                            except:
                                continue

                        if rescued_calls:
                            logger.info(
                                f"Rescued {len(rescued_calls)} tool calls from text in model {decision.model_id}"
                            )
                            response["tool_calls"] = rescued_calls
                            # Clear leaked syntax from text so it doesn't trigger "Content Leak" validation
                            response["text"] = ""
                            response["finish_reason"] = "tool_calls"

                    # --- RESPONSE VALIDATION (STRICT GATEWAY) ---
                    is_valid = True
                    failure_reason = None

                    if not response or not isinstance(response, dict):
                        is_valid = False
                        failure_reason = "Empty or malformed response object"
                    else:
                        # Rule 1: Content Leak Check
                        # Models must not leak raw tool syntax into the standard content block
                        resp_text = str(response.get("text", "")).strip()
                        # Strict patterns that indicate native tag hallucinations instead of API usage
                        leak_patterns = [r"<｜tool", r"<function", r"✿", r"<tool_call>"]
                        for pattern in leak_patterns:
                            if re.search(pattern, resp_text):
                                is_valid = False
                                failure_reason = (
                                    f"Content leak detected: hallucinated {pattern}"
                                )
                                break

                        # Rule 2 & 3: JSON Arguments & Ghost Tool Check
                        if is_valid and response.get("tool_calls"):
                            # Get expected tool names from original request to ensure strict adherence
                            requested_tool_names = []
                            if request.parameters and "tools" in request.parameters:
                                for t in request.parameters["tools"]:
                                    name = t.get("function", {}).get("name")
                                    if name:
                                        requested_tool_names.append(name)

                            for tc in response["tool_calls"]:
                                # Rule 3: Ghost Tool Check
                                tool_name = tc.get("function", {}).get("name")
                                if tool_name not in requested_tool_names:
                                    is_valid = False
                                    failure_reason = f"Ghost tool called: {tool_name}"
                                    break

                                # Rule 2: JSON Arguments Check
                                # The arguments field must be a perfectly parseable JSON string
                                args = tc.get("function", {}).get("arguments")
                                try:
                                    if isinstance(args, str):
                                        json.loads(args)
                                except (json.JSONDecodeError, TypeError):
                                    is_valid = False
                                    failure_reason = (
                                        f"Malformed tool arguments for {tool_name}"
                                    )
                                    break

                    if not is_valid:
                        logger.warning(
                            f"Model {decision.model_id} failed strict validation: {failure_reason}. Escalating..."
                        )
                        self.load_balancer.record_failure(decision.model_id)

                        # Silently send negative feedback to Reality Check for the hallucination
                        if decision.reality_check_id:
                            try:
                                rc_id_str = str(decision.reality_check_id)
                                async with httpx.AsyncClient() as client:
                                    url = (
                                        REALITY_ROUTING_URL
                                        if strategy == "expected_utility"
                                        else REALITY_REROUTING_URL
                                    )
                                    await client.post(
                                        f"{url}/feedback",
                                        json={
                                            "decision_id": int(rc_id_str),
                                            "feedback": 0,
                                        },
                                        headers={
                                            "x-api-key": get_reality_check_key(url)
                                        },
                                        timeout=2.0,
                                    )
                            except Exception as fe:
                                logger.error(f"Auto-negative feedback failed: {fe}")

                        continue

                    # Record success in circuit breaker
                    self.load_balancer.record_success(decision.model_id)

                    # --- STRIP FRAGILE THOUGHT SIGNATURES ---
                    if response.get("tool_calls"):
                        for tc in response["tool_calls"]:
                            if (
                                isinstance(tc, dict)
                                and "id" in tc
                                and isinstance(tc["id"], str)
                                and "__thought__" in tc["id"]
                            ):
                                tc["id"] = tc["id"].split("__thought__")[0]

                    # --- RESPONSE VALIDATION & SMART CONTINUATION ---
                    resp_text = str(response.get("text", "")).strip()
                    finish_reason = response.get("finish_reason")

                    # Attempt continuation if truncated
                    continuation_count = 0
                    max_continuations = 4

                    while (
                        finish_reason == "length"
                        and continuation_count < max_continuations
                    ):
                        logger.info(
                            f"Model {decision.model_id} truncated output. Attempting continuation {continuation_count + 1}/{max_continuations}..."
                        )

                        # Prepare continuation request
                        cont_messages = []
                        if request.parameters and "messages" in request.parameters:
                            cont_messages = list(request.parameters["messages"])
                        else:
                            cont_messages = [{"role": "user", "content": request.query}]

                        # Add the partial response so far and the continuation prompt
                        # If the model natively started a tool call, we need to pass the partial JSON
                        partial_content = response.get("text", "")
                        if not partial_content and response.get("tool_calls"):
                            # This was a partial native tool call that hit max_tokens
                            try:
                                tc = response["tool_calls"][0]
                                args = tc.get("function", {}).get("arguments", "")
                                partial_content = f"```json\n{args}"
                            except:
                                pass

                        cont_messages.append(
                            {"role": "assistant", "content": partial_content}
                        )
                        cont_messages.append(
                            {
                                "role": "user",
                                "content": "Continue exactly where you left off.",
                            }
                        )

                        cont_request = RoutingRequest(
                            query="Continue",
                            agent_id=request.agent_id,
                            parameters={
                                **request.parameters,
                                "messages": cont_messages,
                            }
                            if request.parameters
                            else {"messages": cont_messages},
                        )

                        try:
                            cont_response = await adapter.forward_request(cont_request)

                            # Stitch results
                            response["text"] = response.get(
                                "text", ""
                            ) + cont_response.get("text", "")
                            if "usage" in response and "usage" in cont_response:
                                response["usage"]["completion_tokens"] += cont_response[
                                    "usage"
                                ].get("completion_tokens", 0)
                                response["usage"]["total_tokens"] += cont_response[
                                    "usage"
                                ].get("total_tokens", 0)

                            finish_reason = cont_response.get("finish_reason")
                            response["finish_reason"] = finish_reason
                            resp_text = str(response["text"]).strip()
                            continuation_count += 1
                        except Exception as ce:
                            logger.error(f"Continuation failed: {ce}")
                            # Record failure for circuit breaker
                            self.load_balancer.record_failure(decision.model_id)
                            break

                    if has_tools and not response.get("tool_calls"):
                        import json as _json
                        import re as _re
                        import uuid as _uuid

                        # Try to parse the interceptor's JSON back into a tool_call
                        text_to_parse = response.get("text", "")
                        json_match = _re.search(
                            r"```(?:json)?\s*(\{.*?\})\s*```", text_to_parse, _re.DOTALL
                        )
                        if json_match:
                            json_str = json_match.group(1)
                        else:
                            json_str = text_to_parse.strip()
                            # Find first { and last }
                            start = json_str.find("{")
                            end = json_str.rfind("}")
                            if start != -1 and end != -1:
                                json_str = json_str[start : end + 1]

                        try:
                            if json_str:
                                parsed_args = _json.loads(json_str)
                                tool_name = "unknown"
                                if "tools_schema" in locals() and tools_schema:
                                    tool_name = tools_schema[0]["function"]["name"]

                                # Sometimes models include function name in JSON
                                if "name" in parsed_args and "arguments" in parsed_args:
                                    tool_name = parsed_args["name"]
                                    parsed_args = parsed_args["arguments"]

                                response["tool_calls"] = [
                                    {
                                        "id": f"call_{_uuid.uuid4().hex[:8]}",
                                        "type": "function",
                                        "function": {
                                            "name": tool_name,
                                            "arguments": _json.dumps(parsed_args),
                                        },
                                    }
                                ]
                                response["finish_reason"] = "tool_calls"
                                # We can optionally clear the text if we successfully parsed tools
                                response["text"] = ""
                                resp_text = ""
                        except Exception as e:
                            pass

                    is_empty = not resp_text and not response.get("tool_calls")
                    is_truncated = finish_reason == "length"

                    # --- FORMATTING & SYNTAX VALIDATION ---
                    is_malformed = False
                    is_lazy = False
                    is_refusal = False

                    # 1. Unclosed Markdown code blocks (odd number of ```)
                    if resp_text.count("```") % 2 != 0:
                        is_malformed = True

                    # Extract JSON blocks and validate
                    json_blocks = re.findall(
                        r"```json\s*(.*?)\s*```", resp_text, re.DOTALL
                    )
                    for block in json_blocks:
                        try:
                            json.loads(block)
                        except json.JSONDecodeError:
                            is_malformed = True

                    # 2. Unclosed diff/search/XML blocks
                    has_search = "<<<<<<<" in resp_text
                    has_replace = "=======" in resp_text
                    has_end = ">>>>>>>" in resp_text
                    if (has_search or has_replace or has_end) and not (
                        has_search and has_replace and has_end
                    ):
                        is_malformed = True

                    tags = re.findall(r"<([a-zA-Z0-9_-]+)[^>]*>", resp_text)
                    for tag in set(tags):
                        if tag.lower() in [
                            "thought",
                            "command",
                            "invoke",
                            "action",
                            "tool_call",
                            "function",
                            "answer",
                            "search",
                        ]:
                            open_count = len(
                                re.findall(rf"<{tag}\b[^>]*>", resp_text, re.IGNORECASE)
                            )
                            close_count = len(
                                re.findall(rf"</{tag}>", resp_text, re.IGNORECASE)
                            )
                            if open_count != close_count:
                                is_malformed = True

                    if "<search>" in resp_text and "</search>" not in resp_text:
                        is_malformed = True

                    # 3. Laziness detection
                    lazy_patterns = [
                        "// ...",
                        "# ...",
                        "<!-- ...",
                        "... existing",
                        "... rest",
                        "... remaining",
                    ]
                    lower_resp = resp_text.lower()
                    if any(p in lower_resp for p in lazy_patterns):
                        is_lazy = True

                    # 4. Refusal detection
                    refusal_patterns = [
                        "i'm sorry",
                        "i am sorry",
                        "as an ai",
                        "as a language model",
                        "i cannot fulfill",
                        "i can't fulfill",
                        "i cannot execute",
                        "i am unable to",
                    ]
                    if (
                        any(p in lower_resp for p in refusal_patterns)
                        and len(resp_text) < 500
                    ):
                        is_refusal = True

                    # 5. Tool Call Validation
                    if response.get("tool_calls"):
                        for tc in response["tool_calls"]:
                            if "function" in tc and "arguments" in tc["function"]:
                                try:
                                    json.loads(tc["function"]["arguments"])
                                except json.JSONDecodeError:
                                    is_malformed = True

                    # 6. Additional Truncation Check: Check for partial code blocks at the very end
                    if (resp_text.endswith("`") and not resp_text.endswith("```")) or (
                        resp_text.endswith("``") and not resp_text.endswith("```")
                    ):
                        is_truncated = True

                    # 7. Check for partial JSON/XML structures at the end
                    if not is_malformed:
                        if re.search(r"[{\[]\s*$", resp_text):
                            is_malformed = True
                        if re.search(r"<\w+[^>]*$", resp_text) or re.search(
                            r'"\w+"\s*:\s*$', resp_text
                        ):
                            is_malformed = True

                        # 8. Abrupt Ending Detection: Check for trailing conjunctions or punctuation
                        if re.search(
                            r"\b(and|the|a|an|of|to|with|for|in|on|at|by|from|but|or)\.?\s*$",
                            lower_resp,
                        ):
                            is_truncated = True

                    response["is_malformed"] = is_malformed
                    response["is_lazy"] = is_lazy
                    response["is_refusal"] = is_refusal

                    if (
                        is_empty
                        or is_truncated
                        or is_malformed
                        or is_lazy
                        or is_refusal
                    ):
                        if is_empty:
                            status = "empty"
                        elif is_truncated:
                            status = "still truncated after continuation"
                        elif is_malformed:
                            status = "malformed"
                        elif is_lazy:
                            status = "lazy"
                        else:
                            status = "refusal"

                        logger.warning(
                            f"Model {decision.model_id} returned {status} response. Escalating..."
                        )

                        # Send immediate negative feedback for poor quality
                        if decision.reality_check_id:
                            try:
                                rc_id_str = str(decision.reality_check_id)
                                logger.info(
                                    f"Sending auto-negative feedback for {status} response (decision {rc_id_str})"
                                )
                                async with httpx.AsyncClient() as client:
                                    url = (
                                        REALITY_ROUTING_URL
                                        if strategy == "expected_utility"
                                        else REALITY_REROUTING_URL
                                    )
                                    fb_resp = await client.post(
                                        f"{url}/feedback",
                                        json={
                                            "decision_id": int(rc_id_str),
                                            "feedback": 0,
                                        },
                                        headers={
                                            "x-api-key": get_reality_check_key(url)
                                        },
                                        timeout=2.0,
                                    )
                                    fb_status = fb_resp.status_code
                                    fb_text = fb_resp.text
                                    logger.info(
                                        f"Auto-feedback (quality) response: {fb_status}"
                                    )
                                    if fb_status != 200:
                                        logger.warning(
                                            f"Auto-feedback error body: {fb_text}"
                                        )
                            except Exception as fe:
                                logger.error(f"Auto-feedback failed: {fe}")

                        self.load_balancer.update_metrics(
                            decision.model_id, success=False
                        )
                        # Log the failure and continue to next model in ranked list
                        actual_cost = (
                            response.get("cost", decision.cost)
                            if isinstance(response, dict) and response.get("cost")
                            else decision.cost
                        )
                        self.log_routing_decision(
                            decision.model_copy(
                                update={"time": elapsed_time, "cost": actual_cost}
                            ),
                            request,
                            response,
                            db,
                            routing_context,
                            self.extract_coding_features(
                                request, decision.model_id, response
                            ),
                            "unhappy",
                        )
                        continue

                    self.load_balancer.update_metrics(decision.model_id, success=True)

                    # --- TIERED ASSESSMENT LOGIC ---
                    if strategy == "tiered_assessment" and response:
                        # Extract full set of features including logprobs and confidence
                        final_features = self.extract_coding_features(
                            request, decision.model_id, response
                        )
                        try:
                            # 0. Fast local confidence check
                            local_confidence = final_features.get("confidence", 0.0)
                            if local_confidence > 0.90:
                                logger.info(
                                    f"Model {decision.model_id} high confidence ({local_confidence:.4f}). Stopping tiered assessment early."
                                )
                                break

                            # 1. Calculate p_actual via /decide endpoint (Expert Mode)
                            async with httpx.AsyncClient() as client:
                                # Post-hoc assessment for tiered rerouting always uses REALITY_REROUTING_URL
                                rc_resp = await client.post(
                                    f"{REALITY_REROUTING_URL}/decide",
                                    json={"features": final_features},
                                    headers={
                                        "x-api-key": get_reality_check_key(
                                            REALITY_REROUTING_URL
                                        )
                                    },
                                    timeout=3.0,
                                )
                                if rc_resp.status_code == 200:
                                    rc_data = rc_resp.json()
                                    p_actual = rc_data.get("prob_true", 0.5)
                                    # Update decision with the post-hoc assessment ID for accurate feedback loop
                                    decision.reality_check_id = rc_data.get(
                                        "decision_id"
                                    )

                                    logger.info(
                                        f"Post-hoc assessment for {decision.model_id}: p_actual={p_actual:.4f}, id={decision.reality_check_id}"
                                    )
                                    # 2. Check if we should stop vs escalate
                                    current_idx = ranked_decisions.index(decision)
                                    if current_idx < len(ranked_decisions) - 1:
                                        next_best = ranked_decisions[current_idx + 1]

                                        # Use the formula: (p_next - p_actual) * R > c_next + t_next
                                        # Assuming p_next = 1.0 for high-end escalation comparison
                                        u_stop = (
                                            p_actual * self.utility_calculator.reward
                                        )
                                        eu_continue = self.utility_calculator.calculate_expected_utility(
                                            next_best.cost, next_best.time, 1.0
                                        )

                                        if u_stop < eu_continue:
                                            logger.info(
                                                f"Escalating from {decision.model_id}: u_stop({u_stop:.4f}) < eu_continue({eu_continue:.4f})"
                                            )
                                            # Log this attempt before moving to next model
                                            actual_cost = (
                                                response.get("cost", decision.cost)
                                                if isinstance(response, dict)
                                                and response.get("cost") is not None
                                                else decision.cost
                                            )
                                            self.log_routing_decision(
                                                decision.model_copy(
                                                    update={
                                                        "time": elapsed_time,
                                                        "cost": actual_cost,
                                                    }
                                                ),
                                                request,
                                                response,
                                                db,
                                                routing_context,
                                                final_features,
                                                None,
                                            )
                                            continue

                                        logger.info(
                                            f"Stopping at {decision.model_id}: u_stop({u_stop:.4f}) >= eu_continue({eu_continue:.4f})"
                                        )
                                else:
                                    error_body = rc_resp.text
                                    logger.warning(
                                        f"Post-hoc assessment failed with {rc_resp.status_code}: {error_body}"
                                    )
                                    # Fallback to local confidence if RC fails
                                    if local_confidence > 0:
                                        p_actual = local_confidence
                                        current_idx = ranked_decisions.index(decision)

                        except Exception as e:
                            logger.error(f"Post-hoc tiered assessment failed: {e}")

                        # If we reached here, this model is deemed sufficient, stop escalation.
                        break

                    # Log successful decision
                    actual_cost = (
                        response.get("cost", decision.cost)
                        if isinstance(response, dict) and response.get("cost")
                        else decision.cost
                    )
                    self.log_routing_decision(
                        decision.model_copy(
                            update={"time": elapsed_time, "cost": actual_cost}
                        ),
                        request,
                        response,
                        db,
                        routing_context,
                        self.extract_coding_features(
                            request, decision.model_id, response
                        ),
                        None,
                    )
                    return RoutingResponse(
                        model_id=decision.model_id,
                        model_name=decision.name,
                        expected_utility=decision.expected_utility,
                        cost=actual_cost,
                        time=elapsed_time,
                        probability=decision.probability,
                        response=response,
                        decision_log={},
                    )

                except Exception as e:
                    logger.error(f"Error calling model {decision.model_id}: {e}")
                    last_error = str(e)
                    self.load_balancer.record_failure(decision.model_id)
                    continue

            raise HTTPException(
                status_code=500,
                detail=f"All models failed. Last error: {last_error or 'Unknown'}",
            )

        finally:
            db.close()

    async def run_capability_probes(self):
        """Background task to probe model capabilities and update local cache."""
        logger.info(f"Starting capability probes for {len(self.adapters)} models...")
        for model_id, adapter in self.adapters.items():
            try:
                # This will probe and update the capability_manager cache
                caps = await capability_manager.probe_model(model_id, adapter)

                # Update model info with discovered capabilities
                if model_id in self.models and caps:
                    self.models[model_id]["supports_function_calling"] = caps.get(
                        "supports_tools", False
                    )
                    self.models[model_id]["supports_logprobs"] = caps.get(
                        "supports_logprobs", False
                    )
                    logger.debug(
                        f"Updated capabilities for {model_id}: tools={caps.get('supports_tools')}, logprobs={caps.get('supports_logprobs')}"
                    )
            except Exception as e:
                logger.error(f"Failed to probe capabilities for {model_id}: {e}")
        logger.info("Capability probing cycle complete.")


router_core = RouterCore()


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    stream: bool = False
    agent_id: Optional[str] = "default"
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    stop: Optional[Union[str, List[str]]] = None
    response_format: Optional[Dict[str, Any]] = None


class CompletionRequest(BaseModel):
    model: str
    prompt: Union[str, List[str]]
    stream: bool = False
    agent_id: Optional[str] = "default"
    suffix: Optional[str] = None
    max_tokens: Optional[int] = 16
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    logprobs: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None


class ChatCompletionResponse(BaseModel):
    id: str
    choices: List[Dict[str, Any]]
    model: str
    usage: Dict[str, int]


@router.get("/models")
async def list_models():
    # Return available models
    models = [{"id": mid, "object": "model"} for mid in load_balancer.get_models()]

    # Identify as RealityRouter and expose aggregate capabilities
    all_caps = {"supports_tools": False, "supports_logprobs": False}
    for mid in load_balancer.get_models():
        caps = capability_manager.get_capabilities(mid)
        if caps:
            if caps.get("supports_tools"):
                all_caps["supports_tools"] = True
            if caps.get("supports_logprobs"):
                all_caps["supports_logprobs"] = True

    models.insert(
        0,
        {
            "id": "RealityRouter",
            "object": "model",
            "owned_by": "confidentia-ai",
            "capabilities": all_caps,
        },
    )

    return {"data": models}


@router.get("/.well-known/agent-card.json")
async def get_agent_card():
    """Expose RealityRouter capabilities for dynamic agent discovery"""
    discovered_models = load_balancer.get_models()
    supports_tools = any(
        capability_manager.get_capabilities(m).get("supports_tools", False)
        for m in discovered_models
        if capability_manager.get_capabilities(m)
    )

    return {
        "name": "RealityRouter",
        "description": "Intelligent routing system for LLM requests with MCP support",
        "version": "1.0.0",
        "capabilities": {
            "routing_strategies": ["expected_utility", "round_robin", "weighted"],
            "mcp_translation": True,
            "dynamic_probing": True,
            "supported_features": {
                "tools": supports_tools,
                "sentiment_feedback": True,
            },
            "mcp_tools": [
                {
                    "name": "codebase-edit",
                    "description": "Edit codebase across multiple files",
                },
                {
                    "name": "filesystem-search",
                    "description": "Search for files and content in the filesystem",
                },
                {
                    "name": "mcp-proxy",
                    "description": "Proxy requests to other MCP servers",
                },
            ],
        },
        "endpoints": {
            "chat_completions": "/v1/chat/completions",
            "models": "/v1/models",
            "metrics": "/metrics",
        },
    }


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    Authorization: str = Header(None),
):
    try:
        # Map ChatCompletionRequest to RoutingRequest
        # Support agents that send messages as lists of content blocks
        last_msg = request.messages[-1] if request.messages else {"content": ""}
        content = last_msg.get("content", "")
        if isinstance(content, list):
            query_text = ""
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    query_text += block.get("text", "")
            if not query_text and content:
                query_text = str(content)
        else:
            query_text = str(content)

        routing_req = RoutingRequest(
            query=query_text,
            agent_id=request.agent_id,
            parameters=request.model_dump(exclude={"agent_id"}),
        )

        # Use the global router_core instance
        core = router_core

        # Streaming response handling (OpenAI SSE format)
        async def stream_generator():
            chunk_id = f"chatcmpl-{int(time.time())}"

            # Create a task for routing so we can send pings
            routing_task = asyncio.create_task(core.route_request(routing_req))

            while not routing_task.done():
                # Send a comment as keep-alive ping every 2 seconds
                # Some clients (like Hermes) use these to prevent timeouts
                yield ": ping\n\n"
                await asyncio.sleep(2)

            routing_rsp = await routing_task

            # Extract content and tool calls
            content = (
                routing_rsp.response.get("text", "")
                if isinstance(routing_rsp.response, dict)
                else ""
            ) or (
                routing_rsp.response.get("reasoning_content", "")
                if isinstance(routing_rsp.response, dict)
                else ""
            )

            tool_calls = (
                routing_rsp.response.get("tool_calls")
                if isinstance(routing_rsp.response, dict)
                else None
            )

            finish_reason = (
                routing_rsp.response.get("finish_reason")
                if isinstance(routing_rsp.response, dict)
                else "stop"
            )

            # Send the actual data chunk
            chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": routing_rsp.model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": tool_calls,
                        },
                        "finish_reason": finish_reason,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        if request.stream:
            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        routing_rsp = await core.route_request(routing_req)

        # Ensure OpenAI compatibility for usage details
        usage = (
            routing_rsp.response.get("usage", {})
            if isinstance(routing_rsp.response, dict)
            else {}
        )
        if usage:
            if "prompt_tokens_details" not in usage:
                usage["prompt_tokens_details"] = {"cached_tokens": 0}
            if "completion_tokens_details" not in usage:
                usage["completion_tokens_details"] = {"reasoning_tokens": 0}

        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": routing_rsp.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            routing_rsp.response.get("text", "")
                            if isinstance(routing_rsp.response, dict)
                            else ""
                        )
                        or (
                            routing_rsp.response.get("reasoning_content", "")
                            if isinstance(routing_rsp.response, dict)
                            else ""
                        ),
                        "tool_calls": routing_rsp.response.get("tool_calls")
                        if isinstance(routing_rsp.response, dict)
                        else None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": usage,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/completions")
async def completions(
    request: CompletionRequest,
    Authorization: str = Header(None),
):
    try:
        # Map CompletionRequest to RoutingRequest
        prompt_text = (
            request.prompt
            if isinstance(request.prompt, str)
            else "\n".join(request.prompt)
        )
        routing_req = RoutingRequest(
            query=prompt_text,
            agent_id=request.agent_id,
            parameters=request.model_dump(exclude={"agent_id"}),
        )

        core = router_core

        # Streaming response handling (OpenAI SSE format)
        async def stream_generator():
            chunk_id = f"cmpl-{int(time.time())}"

            # Send keep-alive pings during routing
            routing_task = asyncio.create_task(core.route_request(routing_req))

            while not routing_task.done():
                yield ": ping\n\n"
                await asyncio.sleep(2)

            routing_rsp = await routing_task

            content = (
                routing_rsp.response.get("text", "")
                if isinstance(routing_rsp.response, dict)
                else ""
            ) or (
                routing_rsp.response.get("reasoning_content", "")
                if isinstance(routing_rsp.response, dict)
                else ""
            )

            finish_reason = (
                routing_rsp.response.get("finish_reason", "stop")
                if isinstance(routing_rsp.response, dict)
                else "stop"
            )

            chunk = {
                "id": chunk_id,
                "object": "text_completion",
                "created": int(time.time()),
                "model": routing_rsp.model_id,
                "choices": [
                    {
                        "text": content,
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": finish_reason,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        if request.stream:
            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        routing_rsp = await core.route_request(routing_req)

        # Ensure OpenAI compatibility for usage details
        usage = (
            routing_rsp.response.get("usage", {})
            if isinstance(routing_rsp.response, dict)
            else {}
        )
        if usage:
            if "prompt_tokens_details" not in usage:
                usage["prompt_tokens_details"] = {"cached_tokens": 0}
            if "completion_tokens_details" not in usage:
                usage["completion_tokens_details"] = {"reasoning_tokens": 0}

        return {
            "id": f"cmpl-{int(time.time())}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": routing_rsp.model_id,
            "choices": [
                {
                    "text": (
                        routing_rsp.response.get("text", "")
                        if isinstance(routing_rsp.response, dict)
                        else ""
                    )
                    or (
                        routing_rsp.response.get("reasoning_content", "")
                        if isinstance(routing_rsp.response, dict)
                        else ""
                    ),
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": routing_rsp.response.get("finish_reason", "stop")
                    if isinstance(routing_rsp.response, dict)
                    else "stop",
                }
            ],
            "usage": usage,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
