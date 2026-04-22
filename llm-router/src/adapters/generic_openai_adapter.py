"""
Generic OpenAI-compatible adapter for LLM routing system
"""

import os
from typing import Any, Dict

import openai

from src.adapters.base_adapter import BaseAdapter
from src.models.routing import RoutingRequest


class GenericOpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI-compatible models (Ollama, Together, vLLM, etc)"""

    def __init__(
        self,
        model_name: str = "Generic OpenAI",
        api_key: str = None,
        base_url: str = None,
        default_model: str = None,
    ):
        """
        Initialize the adapter
        """
        super().__init__(model_name, api_key)

        self.api_key = api_key or os.getenv("CUSTOM_LLM_API_KEY", "sk-dummy")
        self.base_url = base_url or os.getenv(
            "CUSTOM_LLM_BASE_URL", "http://localhost:11434/v1"
        )
        self.default_model = default_model or os.getenv("CUSTOM_LLM_MODEL", "llama2")

        # Initialize OpenAI async client pointing to the custom base URL
        base = self.base_url
        if "11434" in base and not base.endswith("v1"):
            base = base.rstrip("/") + "/v1"

        self.client = openai.AsyncOpenAI(api_key=self.api_key, base_url=base)

    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        try:
            messages = [{"role": "user", "content": request.query}]

            req_model = self.default_model
            # If the user explicitly requested a model, let's just use what we have configured for THIS adapter
            params = {
                "model": req_model,
                "logprobs": True,
                "top_logprobs": 5,
                "messages": messages,
                "max_tokens": 1000,
                "temperature": 0.7,
            }

            if request.parameters:
                temp_params = dict(request.parameters)
                if "messages" in temp_params:
                    params["messages"] = temp_params["messages"]
                    del temp_params["messages"]

                # NEVER overwrite the mapped model name for custom endpoints
                if "model" in temp_params:
                    del temp_params["model"]

                params.update(temp_params)

                # Filter out parameters known to cause 400 errors on certain providers (e.g. Gemini)
                unsupported_params = ["frequency_penalty", "presence_penalty"]
                if (
                    "generativelanguage.googleapis.com" in self.base_url
                    or "gemini" in str(self.default_model).lower()
                ):
                    for p in unsupported_params:
                        if p in params:
                            del params[p]

            response = await self.client.chat.completions.create(**params)

            message = response.choices[0].message
            result = {
                "text": message.content,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "model": response.model,
                "finish_reason": response.choices[0].finish_reason,
            }
            if hasattr(message, "tool_calls") and message.tool_calls:
                result["tool_calls"] = []
                for tc in message.tool_calls:
                    result["tool_calls"].append({
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    })

            # Extract logprobs if available
            try:
                if hasattr(response.choices[0], 'logprobs') and response.choices[0].logprobs:
                    content_logprobs = response.choices[0].logprobs.content
                    if content_logprobs:
                        import math
                        import numpy as np
                        
                        probs = [math.exp(token.logprob) for token in content_logprobs]
                        logprobs = [token.logprob for token in content_logprobs]
                        
                        result["logprobs_mean"] = float(np.mean(logprobs)) if logprobs else 0.0
                        result["logprobs_std"] = float(np.std(logprobs)) if logprobs else 0.0
                        
                        # Calculate entropy of top logprobs for the sequence
                        entropies = []
                        for token in content_logprobs:
                            if hasattr(token, 'top_logprobs') and token.top_logprobs:
                                # -sum(p * log(p))
                                e = -sum(math.exp(t.logprob) * t.logprob for t in token.top_logprobs)
                                entropies.append(e)
                        
                        result["entropy"] = float(np.mean(entropies)) if entropies else 0.0
                        
                        # Use average probability as confidence
                        result["confidence"] = float(np.mean(probs)) if probs else 0.0
                        
                        if len(logprobs) > 0:
                            result["first_token_logprob"] = logprobs[0]
                        if len(logprobs) > 1:
                            result["second_token_logprob"] = logprobs[1]
            except Exception as e:
                pass # Ignore logprob parsing errors

            return result

        except Exception as e:
            raise Exception(f"Error calling Custom OpenAI API: {str(e)}")

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "name": self.model_name,
            "provider": "Custom OpenAI-Compatible",
            "type": "chat",
            "description": "Custom LLM API implementation",
        }