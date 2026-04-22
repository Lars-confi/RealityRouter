"""
OpenAI adapter for LLM routing system
"""

import os
from typing import Any, Dict

import openai

from src.adapters.base_adapter import BaseAdapter
from src.models.routing import RoutingRequest, RoutingResponse


class OpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI models"""

    def __init__(self, api_key: str = None):
        """
        Initialize the OpenAI adapter

        Args:
            api_key: OpenAI API key (will use environment variable if not provided)
        """
        super().__init__("OpenAI", api_key)

        # Use environment variable if no key provided
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError("OpenAI API key is required")

        self.api_key = api_key

        # Initialize OpenAI async client
        self.client = openai.AsyncOpenAI(api_key=api_key)

    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        """
        Forward a request to OpenAI

        Args:
            request: RoutingRequest to forward

        Returns:
            Response from OpenAI
        """
        try:
            # Prepare the messages for the OpenAI API
            messages = [{"role": "user", "content": request.query}]

            # Prepare parameters
            params = {
                "model": getattr(self, "default_model", "gpt-3.5-turbo"),
                "logprobs": True,
                "top_logprobs": 5,
                "messages": messages,
                "max_tokens": 1000,
                "temperature": 0.7,
            }

            # Add any additional parameters from the request
            if request.parameters:
                # If messages are provided in parameters, use those instead
                if "messages" in request.parameters:
                    params["messages"] = request.parameters["messages"]
                    # Remove it from parameters so we don't duplicate when updating
                    temp_params = dict(request.parameters)
                    del temp_params["messages"]
                    params.update(temp_params)
                else:
                    params.update(request.parameters)

            # Make the API call asynchronously
            response = await self.client.chat.completions.create(**params)

            # Format the response
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
            raise Exception(f"Error calling OpenAI API: {str(e)}")

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the OpenAI model

        Returns:
            Model information
        """
        return {
            "name": "OpenAI GPT-3.5 Turbo",
            "provider": "OpenAI",
            "type": "chat",
            "description": "OpenAI's GPT-3.5 Turbo model for conversational AI",
        }