"""
Base adapter class for LLM providers
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from src.models.routing import RoutingRequest, RoutingResponse


class BaseAdapter(ABC):
    """Base class for all LLM adapters"""

    def __init__(self, model_name: str, api_key: Optional[str] = None):
        """
        Initialize the adapter

        Args:
            model_name: Name of the model
            api_key: API key for the provider (optional for local models)
        """
        self.model_name = model_name
        self.api_key = api_key

    @abstractmethod
    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        """
        Forward a request to the LLM provider

        Args:
            request: RoutingRequest to forward

        Returns:
            Response from the LLM provider
        """
        pass

    @abstractmethod
    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the model

        Returns:
            Model information
        """
        pass

    def validate_request(self, request: RoutingRequest) -> bool:
        """
        Validate a routing request

        Args:
            request: RoutingRequest to validate

        Returns:
            True if valid, False otherwise
        """
        if not request.query or len(request.query.strip()) == 0:
            return False
        return True
