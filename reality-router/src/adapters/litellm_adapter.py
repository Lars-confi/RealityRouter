"""
LiteLLM adapter for routing requests to various LLM providers using the litellm library.
"""

from typing import Any, Dict, Optional

import litellm

from ..models.routing import RoutingRequest
from .base_adapter import BaseAdapter


class LiteLLMAdapter(BaseAdapter):
    """Adapter for routing requests via LiteLLM"""

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize the LiteLLM adapter

        Args:
            model_name: Name of the model (LiteLLM format, e.g., 'gpt-4o', 'anthropic/claude-3-opus-20240229')
            api_key: API key for the provider
            base_url: Optional base URL for custom providers
        """
        super().__init__(model_name, api_key)
        self.base_url = base_url

    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        """
        Forward a request to the LLM provider using LiteLLM

        Args:
            request: RoutingRequest to forward

        Returns:
            Normalized response dictionary
        """
        if not self.validate_request(request):
            raise ValueError("Invalid request format")

        params = request.parameters or {}

        # Prepare messages
        messages = params.get("messages")
        if not messages:
            messages = [{"role": "user", "content": request.query}]

        # Prepare LiteLLM arguments
        litellm_args = {
            "model": self.model_name,
            "messages": messages,
        }

        if self.api_key:
            litellm_args["api_key"] = self.api_key
        if self.base_url:
            litellm_args["base_url"] = self.base_url

        # Forward standard completion parameters
        allowed_params = [
            "temperature",
            "max_tokens",
            "max_completion_tokens",
            "top_p",
            "top_k",
            "tools",
            "tool_choice",
            "response_format",
            "stream",
            "frequency_penalty",
            "presence_penalty",
            "stop",
            "logprobs",
            "top_logprobs",
        ]

        for key in allowed_params:
            if key in params:
                litellm_args[key] = params[key]

        try:
            # Call LiteLLM async completion
            response = await litellm.acompletion(**litellm_args)

            # Extract main content
            choice = response.choices[0]
            message = choice.message
            text = message.content or ""
            finish_reason = choice.finish_reason

            # Parse tool calls into the standard dictionary format expected by the router core
            tool_calls = []
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )

            # Extract usage statistics
            usage_dict = {}
            if hasattr(response, "usage") and response.usage:
                usage_dict = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(
                        response.usage, "completion_tokens", 0
                    ),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                }

            # Extract logprobs if available
            logprobs_data = {}
            try:
                if hasattr(choice, "logprobs") and choice.logprobs:
                    content_logprobs = choice.logprobs.content
                    if content_logprobs:
                        import math

                        import numpy as np

                        probs = [math.exp(token.logprob) for token in content_logprobs]
                        logprobs_list = [token.logprob for token in content_logprobs]

                        logprobs_data["logprobs_mean"] = (
                            float(np.mean(logprobs_list)) if logprobs_list else 0.0
                        )
                        logprobs_data["logprobs_std"] = (
                            float(np.std(logprobs_list)) if logprobs_list else 0.0
                        )

                        entropies = []
                        for token in content_logprobs:
                            if hasattr(token, "top_logprobs") and token.top_logprobs:
                                e = -sum(
                                    math.exp(t.logprob) * t.logprob
                                    for t in token.top_logprobs
                                )
                                entropies.append(e)

                        logprobs_data["entropy"] = (
                            float(np.mean(entropies)) if entropies else 0.0
                        )
                        logprobs_data["confidence"] = (
                            float(np.mean(probs)) if probs else 0.0
                        )

                        if len(logprobs_list) > 0:
                            logprobs_data["first_token_logprob"] = logprobs_list[0]
                        if len(logprobs_list) > 1:
                            logprobs_data["second_token_logprob"] = logprobs_list[1]
            except Exception:
                pass

            # Estimate cost using LiteLLM's built-in cost tracker
            try:
                cost = litellm.completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            result = {
                "text": text,
                "finish_reason": finish_reason,
                "tool_calls": tool_calls if tool_calls else None,
                "usage": usage_dict,
                "cost": cost,
                "raw_response": response.model_dump()
                if hasattr(response, "model_dump")
                else dict(response),
            }
            result.update(logprobs_data)
            return result

        except Exception as e:
            # Bubble up the exception to let the router load balancer / fallback logic handle it
            raise e

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the model
        """
        return {
            "model_name": self.model_name,
            "provider": "litellm",
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
        }
