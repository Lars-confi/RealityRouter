"""
Pricing Manager for fetching and caching up-to-date LLM token costs.
"""

import json
import os
import time
from typing import Dict, Optional, Tuple

import httpx

from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# We use the open source LiteLLM pricing dataset which is updated frequently
PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
CACHE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "model_prices.json"
)
CACHE_EXPIRY = 7 * 24 * 60 * 60  # 1 week in seconds


class PricingManager:
    """Manager to fetch, cache, and provide model pricing"""

    def __init__(self):
        self.prices: Dict[str, dict] = {}
        self._load_prices()

    def _load_prices(self):
        """Load prices from local cache or fetch if expired/missing"""
        if os.path.exists(CACHE_FILE):
            # Check if cache is still fresh
            if time.time() - os.path.getmtime(CACHE_FILE) < CACHE_EXPIRY:
                try:
                    with open(CACHE_FILE, "r") as f:
                        self.prices = json.load(f)
                    logger.info("Loaded model prices from local cache.")
                    return
                except Exception as e:
                    logger.warning(f"Failed to read pricing cache: {e}")

        # Cache is missing or expired, fetch from remote
        self._fetch_prices()

    def _fetch_prices(self):
        """Fetch latest prices from remote JSON and update cache"""
        try:
            logger.info("Fetching up-to-date model prices from litellm...")
            # Use synchronous httpx for init to ensure prices are ready immediately
            with httpx.Client() as client:
                resp = client.get(PRICING_URL, timeout=10.0)

            if resp.status_code == 200:
                self.prices = resp.json()

                # Ensure config directory exists
                os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

                # Save to cache file
                with open(CACHE_FILE, "w") as f:
                    json.dump(self.prices, f, indent=2)
                logger.info("Successfully fetched and cached model prices.")
            else:
                logger.warning(
                    f"Failed to fetch prices, status code: {resp.status_code}"
                )
                self._fallback_to_cache()

        except Exception as e:
            logger.error(f"Error fetching model prices: {e}")
            self._fallback_to_cache()

    def _fallback_to_cache(self):
        """Fallback to expired local cache if network fetch fails"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    self.prices = json.load(f)
                logger.info("Fell back to expired local cache due to network error.")
            except Exception:
                pass

    def get_model_pricing(
        self, model_name: str
    ) -> Tuple[Optional[float], Optional[float], bool, Optional[int], Optional[int]]:
        """
        Get prompt cost, completion cost per 1k tokens, function calling support, and context limits.

        Args:
            model_name: The name of the model (e.g. 'gpt-4o', 'gemini-1.5-pro')

        Returns:
            Tuple of (prompt_cost_per_1k, completion_cost_per_1k, supports_function_calling, max_input_tokens, max_tokens)
        """
        if not self.prices:
            return None, None, False, None, None

        model_name_lower = model_name.lower()
        if model_name_lower.startswith("models/"):
            model_name_lower = model_name_lower[7:]

        # 1. Try exact direct match
        if model_name_lower in self.prices:
            return self._extract_costs(self.prices[model_name_lower], model_name_lower)

        # 2. Try matching with common provider prefixes (e.g., 'gemini/gemini-1.5-flash')
        for key in self.prices.keys():
            if key.endswith(f"/{model_name_lower}"):
                return self._extract_costs(self.prices[key], model_name_lower)

        # 3. Try finding a key that contains the model name
        for key in self.prices.keys():
            if model_name_lower in key:
                return self._extract_costs(self.prices[key], model_name_lower)

        return None, None, False, None, None

    def _extract_costs(
        self, price_data: dict, model_name: str = ""
    ) -> Tuple[Optional[float], Optional[float], bool, Optional[int], Optional[int]]:
        """Convert cost per token to cost per 1k tokens, also extract limits"""
        try:
            # LiteLLM stores cost per individual token. We calculate cost per 1k tokens.
            input_cost = price_data.get("input_cost_per_token")
            output_cost = price_data.get("output_cost_per_token")

            p_cost = (input_cost * 1000) if input_cost is not None else None
            c_cost = (output_cost * 1000) if output_cost is not None else None
            supports_function_calling = price_data.get(
                "supports_function_calling", False
            ) or price_data.get("supports_tool_choice", False)

            if not supports_function_calling and (
                "gemini-1.5" in model_name
                or "gemini-2.0" in model_name
                or "gemini-2.5" in model_name
            ):
                supports_function_calling = True
            max_input_tokens = price_data.get("max_input_tokens")
            max_tokens = price_data.get("max_tokens")

            return (
                p_cost,
                c_cost,
                supports_function_calling,
                max_input_tokens,
                max_tokens,
            )
        except Exception:
            return None, None, False, None, None


# Global singleton instance
pricing_manager = PricingManager()
