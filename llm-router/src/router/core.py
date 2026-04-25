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
LLM_ROUTING_URL = (
    "https://llmrouter-api.jollysand-1b9ed42e.swedencentral.azurecontainerapps.io"
)
LLM_REROUTING_URL = (
    "https://llmrerouter-api.jollysand-1b9ed42e.swedencentral.azurecontainerapps.io"
)
REALITY_CHECK_ROUTING_KEY = "f7a2b9c8d1e3f5a2b9c8d1e3f5a2b9c8"
REALITY_CHECK_REROUTING_KEY = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"


def get_reality_check_key(url: str) -> str:
    if url == LLM_ROUTING_URL:
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
    name: str
    reality_check_id: Optional[Union[int, str]] = None
    feedback_required: bool = False


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
                                    and name not in settings.disabled_models
                                ):
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
                                    and name not in settings.disabled_models
                                ):
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
                            if name and ("gpt" in name or "o1" in name):
                                if (
                                    name not in self.models
                                    and name not in settings.disabled_models
                                ):
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
                                    from src.adapters.litellm_adapter import (
                                        LiteLLMAdapter,
                                    )

                                    self.adapters[name] = LiteLLMAdapter(
                                        model_name=name, api_key=openai_key
                                    )
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
                        if (
                            name not in self.models
                            and name not in settings.disabled_models
                        ):
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
                            from src.adapters.litellm_adapter import LiteLLMAdapter

                            self.adapters[name] = LiteLLMAdapter(
                                model_name=f"gemini/{name}", api_key=gemini_key
                            )
                except Exception as e:
                    logger.warning(f"Auto-discovery failed for Gemini: {e}")

            logger.info(f"Total configured and discovered models: {len(self.models)}")

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
        logger.info(f"Ranking models using strategy: {strategy}")
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

            tools_requested = request.parameters and request.parameters.get("tools")

            model_tasks = []
            for mid, info in self.models.items():
                if tools_requested and not info.get("supports_function_calling", False):
                    # We added the fallback interceptor for this exact scenario so it strips tools automatically if unsupported!
                    # Do NOT drop it from the leaderboard, just let the interceptor handle it.
                    pass

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
                        # Strategy 1 (LLM Routing) uses the llmrouter endpoint
                        url = (
                            LLM_ROUTING_URL
                            if strategy == "expected_utility"
                            else LLM_REROUTING_URL
                        )
                        resp = await client.post(
                            f"{url}/decide",
                            json={"features": m["features"]},
                            headers={"x-api-key": get_reality_check_key(url)},
                            timeout=5.0,
                        )
                        if resp.status_code == 200:
                            r = resp.json()
                            prob = r.get("prob_true", 0.5)
                            logger.info(
                                f"Reality Check calibration for {m['id']}: prob={prob:.4f}, id={r.get('decision_id')}"
                            )
                            print(
                                f"Reality Check calibration for {m['id']}: prob={prob:.4f}, id={r.get('decision_id')}"
                            )
                            return {
                                **m,
                                "prob": prob,
                                "rc_id": r.get("decision_id"),
                                "fb_req": r.get("decision", False),
                            }
                        else:
                            error_body = resp.text
                            logger.warning(
                                f"Reality Check API returned {resp.status_code} for {m['id']}: {error_body}"
                            )
                except Exception as e:
                    logger.error(f"Reality Check call failed for {m['id']}: {e}")

                return {**m, "prob": 0.5, "rc_id": None, "fb_req": False}

            results = await asyncio.gather(*[call_rc(m) for m in model_tasks])

            decisions = []
            feedback_candidates = []
            for r in results:
                is_zed = request.agent_id and "zed" in request.agent_id.lower()
                utility = self.utility_calculator.calculate_expected_utility(
                    r["cost"], r["time"], r["prob"]
                )
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
                    name=r["name"],
                    reality_check_id=r["rc_id"],
                    feedback_required=r["fb_req"],
                )
                decisions.append(d)
                if r["fb_req"]:
                    feedback_candidates.append(d)

            if strategy == "tiered_assessment":
                # Start with cheapest models first for tiered escalation
                decisions.sort(key=lambda x: (x.cost, x.time))
            else:
                decisions.sort(key=lambda x: x.expected_utility, reverse=True)

            if feedback_candidates:
                import random

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
                logger.info(f"Updated log entry {log_entry.id} with RC ID and features")

            logger.info(f"Logged routing decision for model {decision.model_id}")
        except Exception as e:
            logger.error(f"Error logging routing decision: {str(e)}")

    async def assess_user_sentiment(self, request: RoutingRequest) -> Optional[str]:
        """Assess user sentiment based on conversation history for feedback"""
        try:
            messages = (request.parameters or {}).get("messages", [])
            logger.debug(
                f"Assessing sentiment for interaction with {len(messages)} messages"
            )
            if len(messages) < 3:
                logger.debug("Not enough messages for sentiment assessment (need >= 3)")
                return None

            def _get_text(content):
                if isinstance(content, str):
                    return content
                if isinstance(content, list) and content:
                    return str(content[0].get("text", ""))
                return ""

            prev_asst = _get_text(messages[-2].get("content", ""))
            curr_user = _get_text(messages[-1].get("content", ""))

            if not prev_asst or not curr_user:
                return None

            # Use the configured sentiment model, or fall back to the cheapest available model
            settings = get_settings()
            sentiment_id = settings.sentiment_model_id

            if not sentiment_id or sentiment_id not in self.adapters:
                sentiment_id = min(
                    self.models.keys(), key=lambda m: self.models[m].get("cost", 0)
                )

            adapter = self.adapters.get(sentiment_id)
            if not adapter:
                return None

            prompt = (
                f"Analyze the following interaction to see if the user was satisfied with the assistant's last response.\n\n"
                f'Assistant\'s Response: "{prev_asst}"\n'
                f'User\'s Follow-up: "{curr_user}"\n\n'
                f"Determine if the user's follow-up indicates they were 'happy' with the previous answer, "
                f"'unhappy' (e.g., they corrected it, complained, or asked for a redo), or if it's 'indeterminate'.\n"
                f"Respond with EXACTLY one word: happy, unhappy, or indeterminate."
            )

            logger.debug(f"Sending sentiment analysis prompt to model {sentiment_id}")
            resp = await adapter.forward_request(
                RoutingRequest(
                    query=prompt, parameters={"max_tokens": 5, "temperature": 0}
                )
            )

            txt = str(resp.get("text", "")).lower().strip()
            # Explicitly check for 'unhappy' first to avoid partial matches
            if "unhappy" in txt:
                sentiment = "unhappy"
            elif "happy" in txt:
                sentiment = "happy"
            else:
                sentiment = "indeterminate"
            logger.info(f"Assessed user sentiment: {sentiment} (raw output: '{txt}')")
            return sentiment
        except Exception as e:
            logger.debug(f"Sentiment assessment failed: {e}")
            return None

    async def route_request(
        self, request: RoutingRequest, strategy: Optional[str] = None
    ) -> RoutingResponse:
        settings = get_settings()
        if strategy is None:
            strategy = settings.default_strategy

        logger.info(f"Routing request with strategy: {strategy}")
        db = SessionLocal()
        try:
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
                        .filter(RoutingLog.agent_id == (request.agent_id or "default"))
                        .filter(RoutingLog.reality_check_id != None)
                        .filter(RoutingLog.user_sentiment == None)
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
                                LLM_ROUTING_URL
                                if fb_strategy == "expected_utility"
                                else LLM_REROUTING_URL
                            )

                            feedback_val = 1 if sentiment == "happy" else 0
                            fb_payload = {
                                "decision_id": int(rc_id_str),
                                "feedback": feedback_val,
                            }

                            logger.info(
                                f"Sending feedback to Reality Check ({fb_strategy}) for decision {rc_id_str}: {sentiment}"
                            )
                            print(
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
                                print(f"Reality Check feedback response: {fb_status}")
                                if fb_status != 200:
                                    logger.warning(f"Feedback error detail: {fb_text}")
                                    print(f"Feedback error detail: {fb_text}")
                        except ValueError:
                            logger.error(
                                f"Invalid reality_check_id format: {last_log.reality_check_id}"
                            )
                except Exception as e:
                    logger.error(f"Error sending feedback to Reality Check: {e}")

            if ranked_decisions:
                title = (
                    " LLM ROUTER: RANKING MODELS "
                    if strategy == "expected_utility"
                    else " LLM REROUTER: RANKING MODELS "
                )
                print("\n" + "=" * 70)
                print(title.center(70, "="))
                print("-" * 70)
                print(f"{'  Model Name (ID)':<42} | {'Utility':>10} | {'Prob':>8}")
                print("-" * 42 + " | " + "-" * 10 + " | " + "-" * 8)
                for d in ranked_decisions:
                    marker = ">>" if d == ranked_decisions[0] else "  "
                    label = d.name if len(d.name) <= 39 else d.name[:36] + "..."
                    print(
                        f"{marker} {label:<39} | {d.expected_utility:>10.4f} | {d.probability:>8.4f}"
                    )
                print("=" * 70 + "\n")

            routing_context = json.dumps([d.model_dump() for d in ranked_decisions])
            last_error = None

            for decision in ranked_decisions:
                adapter = self.adapters.get(decision.model_id)
                if not adapter:
                    continue

                semaphore = self._get_semaphore(decision.model_id)

                try:
                    start_time = time.time()

                    has_tools = bool(
                        request.parameters and request.parameters.get("tools")
                    )
                    if has_tools and not self.models[decision.model_id].get(
                        "supports_function_calling"
                    ):
                        import json

                        tools_schema = request.parameters["tools"]
                        del request.parameters["tools"]
                        if "tool_choice" in request.parameters:
                            del request.parameters["tool_choice"]
                        sys_msg = {
                            "role": "system",
                            "content": "The user has MCP tools available. Please respond with a JSON object that matches the following requested tool schemas:\n"
                            + json.dumps(tools_schema, indent=2),
                        }
                        if request.parameters and "messages" in request.parameters:
                            request.parameters["messages"].insert(0, sys_msg)

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
                            response = await adapter.forward_request(request)
                    else:
                        response = await adapter.forward_request(request)
                    elapsed_time = time.time() - start_time

                    # --- RESPONSE VALIDATION & SMART CONTINUATION ---
                    resp_text = str(response.get("text", "")).strip()
                    finish_reason = response.get("finish_reason")

                    # Record success/failure in circuit breaker
                    if response:
                        self.load_balancer.record_success(decision.model_id)
                    else:
                        self.load_balancer.record_failure(decision.model_id)

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
                                print(
                                    f"Sending auto-negative feedback for {status} response (decision {rc_id_str})"
                                )
                                async with httpx.AsyncClient() as client:
                                    url = (
                                        LLM_ROUTING_URL
                                        if strategy == "expected_utility"
                                        else LLM_REROUTING_URL
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
                                    print(
                                        f"Auto-feedback (quality) response: {fb_status}"
                                    )
                                    if fb_status != 200:
                                        logger.warning(
                                            f"Auto-feedback error body: {fb_text}"
                                        )
                                        print(f"Auto-feedback error body: {fb_text}")
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
                                # Post-hoc assessment for tiered rerouting always uses LLM_REROUTING_URL
                                rc_resp = await client.post(
                                    f"{LLM_REROUTING_URL}/decide",
                                    json={"features": final_features},
                                    headers={
                                        "x-api-key": get_reality_check_key(
                                            LLM_REROUTING_URL
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
                                    print(
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
                                                and response.get("cost")
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
                                        if current_idx < len(ranked_decisions) - 1:
                                            next_best = ranked_decisions[
                                                current_idx + 1
                                            ]
                                            u_stop = (
                                                p_actual
                                                * self.utility_calculator.reward
                                            )
                                            eu_continue = self.utility_calculator.calculate_expected_utility(
                                                next_best.cost, next_best.time, 1.0
                                            )
                                            if u_stop < eu_continue:
                                                logger.info(
                                                    f"Escalating via local fallback from {decision.model_id}: {u_stop:.4f} < {eu_continue:.4f}"
                                                )
                                                actual_cost = (
                                                    response.get("cost", decision.cost)
                                                    if isinstance(response, dict)
                                                    and response.get("cost")
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
                        except Exception as ae:
                            logger.error(f"Assessment escalation check failed: {ae}")
                            # Fallback to local confidence on exception
                            if final_features.get("confidence", 0.0) > 0:
                                p_actual = final_features.get("confidence")
                                current_idx = ranked_decisions.index(decision)
                                if current_idx < len(ranked_decisions) - 1:
                                    next_best = ranked_decisions[current_idx + 1]
                                    u_stop = p_actual * self.utility_calculator.reward
                                    eu_continue = self.utility_calculator.calculate_expected_utility(
                                        next_best.cost, next_best.time, 1.0
                                    )
                                    if u_stop < eu_continue:
                                        logger.info(
                                            f"Escalating via exception fallback from {decision.model_id}: {u_stop:.4f} < {eu_continue:.4f}"
                                        )
                                        actual_cost = (
                                            response.get("cost", decision.cost)
                                            if isinstance(response, dict)
                                            and response.get("cost")
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

                    final_features = self.extract_coding_features(
                        request, decision.model_id, response
                    )
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
                        final_features,
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
                    )
                except Exception as e:
                    logger.error(
                        f"Model {decision.model_id} failed with infrastructure error: {e}"
                    )
                    self.load_balancer.update_metrics(decision.model_id, success=False)

                    self.log_routing_decision(
                        decision,
                        request,
                        {},
                        db,
                        routing_context,
                        self.extract_coding_features(request, decision.model_id),
                    )
                    last_error = e
                    continue

            if last_error:
                raise HTTPException(
                    status_code=502,
                    detail=f"All models failed. Last infrastructure error: {str(last_error)}",
                )
            else:
                raise HTTPException(
                    status_code=500, detail="No models available to handle the request."
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Routing error: {e}")
            raise HTTPException(status_code=500, detail=f"Routing error: {str(e)}")
        finally:
            db.close()

    async def run_capability_probes(self):
        """Runs background capability probes on un-cached models."""
        if not getattr(self, "models_to_probe", None):
            return

        logger.info(
            f"Starting background capability probes for {len(self.models_to_probe)} models..."
        )
        import asyncio

        sem = asyncio.Semaphore(5)
        print(f"DEBUG: Models to probe: {self.models_to_probe}")

        async def probe(model_id):
            async with sem:
                if model_id not in self.adapters:
                    return
                caps = await capability_manager.probe_model(
                    model_id, self.adapters[model_id]
                )
                if model_id in self.models:
                    self.models[model_id]["supports_function_calling"] = caps.get(
                        "supports_tools", False
                    )
                    self.models[model_id]["supports_logprobs"] = caps.get(
                        "supports_logprobs", False
                    )

        tasks = [probe(mid) for mid in self.models_to_probe]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Finished background capability probes.")
        self.models_to_probe = []


class ChatCompletionRequest(BaseModel):
    messages: List[Dict[str, Any]]
    model: Optional[str] = None
    agent_id: Optional[str] = None
    strategy: Optional[str] = None
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    top_p: Optional[float] = 1.0
    frequency_penalty: Optional[float] = 0.0
    presence_penalty: Optional[float] = 0.0
    stop: Optional[Union[str, List[str]]] = None
    stream: Optional[bool] = False

    model_config = {"extra": "allow"}


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    choices: List[Dict[str, Any]]
    created: int
    model: str
    usage: Dict[str, Any]


@router.get("/models")
async def list_models():
    models_list = []
    for model_id, info in router_core.models.items():
        models_list.append(
            {
                "id": model_id,
                "object": "model",
                "created": 1686935002,
                "owned_by": "llm-rerouter",
            }
        )
    return {"object": "list", "data": models_list}


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    x_agent_id: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None),
):
    try:
        query_text = ""
        if request.messages:
            # Extract text content from the last message for reality check features
            last_msg = request.messages[-1].get("content", "")
            if isinstance(last_msg, str):
                query_text = last_msg
            elif isinstance(last_msg, list) and last_msg:
                query_text = " ".join(
                    [
                        str(part.get("text", ""))
                        for part in last_msg
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                )
                if not query_text:
                    query_text = str(last_msg[0].get("text", ""))
            else:
                query_text = str(last_msg)

        if not query_text and request.messages:
            query_text = str(request.messages[-1])

        is_streaming = request.stream
        params = request.model_dump(exclude_none=True)
        strategy = params.pop("strategy", None)
        agent_id = params.pop("agent_id", x_agent_id or user_agent or "default")
        params["stream"] = False
        if "stream_options" in params:
            del params["stream_options"]

        if not is_streaming:
            response = await router_core.route_request(
                RoutingRequest(query=query_text, agent_id=agent_id, parameters=params),
                strategy=strategy,
            )
            created_time = int(datetime.datetime.now().timestamp())
            return {
                "id": f"chatcmpl-{response.model_id}",
                "object": "chat.completion",
                "created": created_time,
                "model": response.model_name,
                "system_fingerprint": "fp_llm_rerouter",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": response.response.get("text", ""),
                            **(
                                {"tool_calls": response.response.get("tool_calls")}
                                if response.response.get("tool_calls")
                                else {}
                            ),
                        },
                        "logprobs": None,
                        "finish_reason": response.response.get("finish_reason", "stop"),
                    }
                ],
                "usage": response.response.get("usage", {}),
            }

        async def stream_generator():
            import asyncio

            router_task = asyncio.create_task(
                router_core.route_request(
                    RoutingRequest(
                        query=query_text, agent_id=agent_id, parameters=params
                    ),
                    strategy=strategy,
                )
            )

            # Send SSE keep-alives while waiting for the full response to prevent read timeouts
            while not router_task.done():
                keepalive_data = {
                    "id": "chatcmpl-keepalive",
                    "object": "chat.completion.chunk",
                    "created": int(datetime.datetime.now().timestamp()),
                    "model": "router",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(keepalive_data)}\n\n"
                await asyncio.sleep(1.0)

            response = router_task.result()
            created_time = int(datetime.datetime.now().timestamp())

            chunk_id = f"chatcmpl-{response.model_id}"
            common = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": response.model_name,
            }
            yield f"data: {json.dumps({**common, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"

            content = response.response.get("text", "")
            if content:
                # Stream content in chunks
                chunk_size = 40
                for j in range(0, len(content), chunk_size):
                    text_chunk = content[j : j + chunk_size]
                    yield f"data: {json.dumps({**common, 'choices': [{'index': 0, 'delta': {'content': text_chunk}, 'finish_reason': None}]})}\n\n"
                    await asyncio.sleep(0.01)

            tool_calls = response.response.get("tool_calls")
            if tool_calls:
                init_tool_calls = []
                for i, tc in enumerate(tool_calls):
                    init_tool_calls.append(
                        {
                            "index": i,
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name", "")
                            },
                        }
                    )
                yield f"data: {json.dumps({**common, 'choices': [{'index': 0, 'delta': {'tool_calls': init_tool_calls}, 'finish_reason': None}]})}\n\n"

                for i, tc in enumerate(tool_calls):
                    args = tc.get("function", {}).get("arguments", "")
                    if args:
                        # Stream arguments in chunks to prevent token timeouts in clients
                        chunk_size = 40
                        for j in range(0, len(args), chunk_size):
                            arg_chunk = args[j : j + chunk_size]
                            yield f"data: {json.dumps({**common, 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': i, 'function': {'arguments': arg_chunk}}]}, 'finish_reason': None}]})}\n\n"
                            await asyncio.sleep(0.01)

            yield f"data: {json.dumps({**common, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': response.response.get('finish_reason', 'stop')}]})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/completions")
async def completions(
    request: ChatCompletionRequest,
    x_agent_id: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None),
):
    try:
        query_text = ""
        if request.messages:
            last_msg = request.messages[-1].get("content", "")
            if isinstance(last_msg, str):
                query_text = last_msg
            elif isinstance(last_msg, list) and last_msg:
                query_text = str(last_msg[0].get("text", ""))
            else:
                query_text = str(last_msg)

        if not query_text and request.model_dump().get("prompt"):
            query_text = str(request.model_dump().get("prompt"))

        is_streaming = request.stream
        params = request.model_dump(exclude_none=True)
        strategy = params.pop("strategy", None)
        agent_id = params.pop("agent_id", x_agent_id or user_agent or "default")
        params["stream"] = False
        if "stream_options" in params:
            del params["stream_options"]

        if not is_streaming:
            response = await router_core.route_request(
                RoutingRequest(query=query_text, agent_id=agent_id, parameters=params),
                strategy=strategy,
            )
            created_time = int(datetime.datetime.now().timestamp())
            return {
                "id": f"cmpl-{response.model_id}",
                "object": "text_completion",
                "created": created_time,
                "model": response.model_name,
                "choices": [
                    {
                        "text": response.response.get("text", ""),
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": response.response.get("finish_reason", "stop"),
                    }
                ],
                "usage": response.response.get("usage", {}),
            }

        async def stream_generator():
            import asyncio

            router_task = asyncio.create_task(
                router_core.route_request(
                    RoutingRequest(
                        query=query_text, agent_id=agent_id, parameters=params
                    ),
                    strategy=strategy,
                )
            )

            # Send SSE keep-alives while waiting for the full response to prevent read timeouts
            while not router_task.done():
                keepalive_data = {
                    "id": "cmpl-keepalive",
                    "object": "text_completion",
                    "created": int(datetime.datetime.now().timestamp()),
                    "model": "router",
                    "choices": [{"text": "", "index": 0, "finish_reason": None}],
                }
                yield f"data: {json.dumps(keepalive_data)}\n\n"
                await asyncio.sleep(1.0)

            response = router_task.result()
            created_time = int(datetime.datetime.now().timestamp())

            chunk_id = f"cmpl-{response.model_id}"
            common = {
                "id": chunk_id,
                "object": "text_completion",
                "created": created_time,
                "model": response.model_name,
            }

            content = response.response.get("text", "")
            if content:
                chunk_size = 40
                for j in range(0, len(content), chunk_size):
                    text_chunk = content[j : j + chunk_size]
                    yield f"data: {json.dumps({**common, 'choices': [{'text': text_chunk, 'index': 0, 'finish_reason': None}]})}\n\n"
                    await asyncio.sleep(0.01)

            yield f"data: {json.dumps({**common, 'choices': [{'text': '', 'index': 0, 'finish_reason': response.response.get('finish_reason', 'stop')}]})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    except Exception as e:
        logger.error(f"Completion error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_models():
    return router_core.models


async def get_metrics():
    return metrics_collector.get_model_stats()


router_core = RouterCore()
