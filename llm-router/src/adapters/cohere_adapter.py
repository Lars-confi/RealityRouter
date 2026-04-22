"""
Cohere adapter for LLM routing system
"""

import os
from typing import Any, Dict

import cohere

from src.adapters.base_adapter import BaseAdapter
from src.models.routing import RoutingRequest, RoutingResponse


class CohereAdapter(BaseAdapter):
    """Adapter for Cohere models"""

    def __init__(self, api_key: str = None):
        """
        Initialize the Cohere adapter

        Args:
            api_key: Cohere API key (will use environment variable if not provided)
        """
        super().__init__("Cohere", api_key)

        # Use environment variable if no key provided
        if not api_key:
            api_key = os.getenv("COHERE_API_KEY")

        if not api_key:
            raise ValueError("Cohere API key is required")

        self.api_key = api_key
        # Initialize Cohere async client
        self.client = cohere.AsyncClient(api_key)

    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        """
        Forward a request to Cohere

        Args:
            request: RoutingRequest to forward

        Returns:
            Response from Cohere
        """
        try:
            # Setup defaults
            temperature = 0.7
            max_tokens = 1000
            model = "command-r-plus"

            # Apply parameters if present
            if request.parameters:
                temperature = request.parameters.get("temperature", temperature)
                max_tokens = request.parameters.get("max_tokens", max_tokens)
                if (
                    "model" in request.parameters
                    and "command" in request.parameters["model"]
                ):
                    model = request.parameters["model"]

            # Prepare the prompt for the Cohere API and call asynchronously
            response = await self.client.chat(
                model=model,  # Default model
                message=request.query,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Extract token counts safely
            prompt_tokens = 0
            completion_tokens = 0
            if hasattr(response, "token_count") and response.token_count:
                prompt_tokens = getattr(response.token_count, "prompt_tokens", 0)
                completion_tokens = getattr(
                    response.token_count, "completion_tokens", 0
                )
            elif (
                hasattr(response, "meta")
                and response.meta
                and hasattr(response.meta, "billed_units")
                and response.meta.billed_units
            ):
                prompt_tokens = getattr(response.meta.billed_units, "input_tokens", 0)
                completion_tokens = getattr(
                    response.meta.billed_units, "output_tokens", 0
                )

            # Format the response
            return {
                "text": response.text,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                "model": getattr(response, "model", model),
                "finish_reason": "complete",
            }

        except Exception as e:
            raise Exception(f"Error calling Cohere API: {str(e)}")

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the Cohere model

        Returns:
            Model information
        """
        return {
            "name": "Cohere Command R+",
            "provider": "Cohere",
            "type": "chat",
            "description": "Cohere's Command R+ model for advanced reasoning tasks",
        }