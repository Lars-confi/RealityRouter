"""
Model Info Manager for fetching and caching model descriptions and capabilities.
"""

import json
import os
import time
from typing import Dict, Optional

import httpx

from src.utils.logger import setup_logger

logger = setup_logger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
APP_HOME = os.getenv("REALITY_ROUTER_HOME", os.path.expanduser("~/.reality_router"))
CACHE_FILE = os.path.join(APP_HOME, "config", "model_info_cache.json")
CACHE_EXPIRY = 14 * 24 * 60 * 60  # 2 weeks in seconds


class ModelInfoManager:
    """Manager to fetch, cache, and provide descriptive metadata about LLM models"""

    def __init__(self):
        self.info_cache: Dict[str, dict] = {}
        self._load_cache()

    def _load_cache(self):
        """Load model info from local cache or fetch if missing/expired"""
        if os.path.exists(CACHE_FILE):
            if time.time() - os.path.getmtime(CACHE_FILE) < CACHE_EXPIRY:
                try:
                    with open(CACHE_FILE, "r") as f:
                        self.info_cache = json.load(f)
                    logger.info("Loaded model descriptions from local cache.")
                    return
                except Exception as e:
                    logger.warning(f"Failed to read model info cache: {e}")

        # Fetch fresh data if cache is empty or expired
        self.refresh_info()

    def refresh_info(self):
        """Fetch latest model metadata from OpenRouter and update cache"""
        try:
            logger.info("Fetching model metadata from OpenRouter...")
            with httpx.Client() as client:
                resp = client.get(OPENROUTER_MODELS_URL, timeout=15.0)

            if resp.status_code == 200:
                data = resp.json()
                models_list = data.get("data", [])

                # We store both by ID and by short name to maximize hit rate
                new_cache = {}
                for m in models_list:
                    m_id = m.get("id", "").lower()
                    m_name = m.get("name", "").lower()

                    description = m.get("description", "No description available.")
                    # Clean up common patterns in descriptions
                    description = description.replace("\n", " ").strip()

                    details = {
                        "description": description,
                        "context_length": m.get("context_length"),
                        "architecture": m.get("architecture", {}).get(
                            "modality", "Text"
                        ),
                        "top_provider": m.get("top_provider", {}).get(
                            "name", "Various"
                        ),
                    }

                    new_cache[m_id] = details
                    # Also index by the part after the provider (e.g. 'claude-3-opus')
                    if "/" in m_id:
                        short_id = m_id.split("/")[-1]
                        if short_id not in new_cache:
                            new_cache[short_id] = details

                self.info_cache = new_cache
                os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
                with open(CACHE_FILE, "w") as f:
                    json.dump(self.info_cache, f, indent=2)
                logger.info(
                    f"Successfully cached metadata for {len(models_list)} models."
                )
            else:
                logger.warning(f"Failed to fetch model metadata: {resp.status_code}")
        except Exception as e:
            logger.error(f"Error refreshing model info: {e}")

    async def get_ollama_description(
        self, model_id: str, custom_url: Optional[str]
    ) -> str:
        """Attempt to fetch a description directly from a local Ollama instance"""
        if not custom_url or "11434" not in custom_url:
            return ""

        try:
            base_url = custom_url.split("/v1")[0]
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}/api/show", json={"name": model_id}, timeout=2.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Prefer the 'details' or 'system' prompt hints if description is missing
                    details = data.get("details", {})
                    family = details.get("family", "")
                    param_size = details.get("parameter_size", "")
                    return f"Local Ollama model. Family: {family}. Parameters: {param_size}."
        except Exception:
            pass
        return ""

    def get_model_description(self, model_id: str) -> str:
        """
        Get a human-readable description of what a model is good for.
        """
        m_id_lower = model_id.lower()

        # 1. Try exact match
        if m_id_lower in self.info_cache:
            return self.info_cache[m_id_lower]["description"]

        # 2. Try partial match (provider/name -> name)
        if "/" in m_id_lower:
            parts = m_id_lower.split("/")
            # Check for deepseek/deepseek-chat -> deepseek-chat
            if parts[-1] in self.info_cache:
                return self.info_cache[parts[-1]]["description"]

        # 3. Handle common patterns
        if "gpt-4o" in m_id_lower:
            return "OpenAI's flagship multimodal model, optimized for high intelligence and speed."
        if "claude-3-5-sonnet" in m_id_lower:
            return "Anthropic's most intelligent and capable model, excelling at coding and reasoning."
        if "deepseek" in m_id_lower:
            if "coder" in m_id_lower:
                return "A specialized model optimized for coding, mathematical reasoning, and technical tasks."
            return "A highly efficient and intelligent general-purpose chat model."

        return "A high-performance language model optimized for agentic workflows."


# Global singleton instance
model_info_manager = ModelInfoManager()
