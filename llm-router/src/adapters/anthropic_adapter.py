"""
Anthropic adapter for LLM routing system
"""

import os
from typing import Any, Dict

import anthropic

from src.adapters.base_adapter import BaseAdapter
from src.models.routing import RoutingRequest, RoutingResponse


class AnthropicAdapter(BaseAdapter):
    """Adapter for Anthropic models"""

    def __init__(self, api_key: str = None):
        """
        Initialize the Anthropic adapter

        Args:
            api_key: Anthropic API key (will use environment variable if not provided)
        """
        super().__init__("Anthropic", api_key)

        # Use environment variable if no key provided
        if not api_key:
            api_key = os.getenv("ANTHROPIC_API_KEY")

        if not api_key:
            raise ValueError("Anthropic API key is required")

        self.api_key = api_key
        # Initialize Anthropic async client
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        """
        Forward a request to Anthropic

        Args:
            request: RoutingRequest to forward

        Returns:
            Response from Anthropic
        """
        try:
            # Prepare parameters
            model = "claude-3-haiku-20240307"
            if request.parameters and "model" in request.parameters:
                # If the user specified an Anthropic model, try to use it
                if "claude" in request.parameters["model"]:
                    model = request.parameters["model"]

            messages = [{"role": "user", "content": request.query}]

            system_prompt = None
            if request.parameters and "messages" in request.parameters:
                messages = []
                for msg in request.parameters["messages"]:
                    if msg.get("role") == "system":
                        if system_prompt is None:
                            system_prompt = msg.get("content", "")
                        else:
                            system_prompt += "\n" + msg.get("content", "")
                    else:
                        messages.append(msg)

            # Translate OpenAI tools to Anthropic format
            anthropic_tools = []
            if request.parameters and "tools" in request.parameters:
                for tool in request.parameters["tools"]:
                    if tool.get("type") == "function":
                        fn = tool.get("function", {})
                        anthropic_tools.append({
                            "name": fn.get("name"),
                            "description": fn.get("description", ""),
                            "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
                        })

            # Prepare args
            kwargs = {
                "model": getattr(self, "default_model", model),
                "max_tokens": request.parameters.get("max_tokens", 1000) if request.parameters else 1000,
                "temperature": request.parameters.get("temperature", 0.7) if request.parameters else 0.7,
                "messages": messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

            # Prepare the message for the Anthropic API asynchronously
            message = await self.client.messages.create(**kwargs)

            # Format the response
            text_content = ""
            tool_calls = []
            for block in message.content:
                if block.type == "text":
                    text_content += block.text
                elif block.type == "tool_use":
                    import json
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input)
                        }
                    })
            
            result = {
                "text": text_content,
                "usage": {
                    "prompt_tokens": message.usage.input_tokens,
                    "completion_tokens": message.usage.output_tokens,
                    "total_tokens": message.usage.input_tokens
                    + message.usage.output_tokens,
                },
                "model": message.model,
                "finish_reason": message.stop_reason,
            }
            if tool_calls:
                result["tool_calls"] = tool_calls
            return result

        except Exception as e:
            raise Exception(f"Error calling Anthropic API: {str(e)}")

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the Anthropic model

        Returns:
            Model information
        """
        return {
            "name": "Anthropic Claude Haiku",
            "provider": "Anthropic",
            "type": "chat",
            "description": "Anthropic's Claude Haiku model for fast, efficient responses",
        }