"""
Gemini adapter for LLM routing system
"""
import os
import openai
from typing import Dict, Any
from src.adapters.base_adapter import BaseAdapter
from src.models.routing import RoutingRequest

class GeminiAdapter(BaseAdapter):
    """Adapter for Google Gemini models via OpenAI compatibility layer"""
    
    def __init__(self, api_key: str = None):
        super().__init__("Gemini", api_key)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required")
        
        # Gemini is fully compatible with the standard OpenAI SDK using this base URL
        self.client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        
    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        try:
            messages = [{"role": "user", "content": request.query}]
            
            model = "gemini-1.5-flash"
            if request.parameters and "model" in request.parameters:
                if "gemini" in request.parameters["model"]:
                    model = request.parameters["model"]
                    
            params = {
                "model": model,
                "messages": messages,
                "max_tokens": 1000,
                "temperature": 0.7
            }
            
            if request.parameters:
                temp_params = dict(request.parameters)
                if "messages" in temp_params:
                    params["messages"] = temp_params["messages"]
                    del temp_params["messages"]
                
                # NEVER overwrite the mapped model name
                if "model" in temp_params:
                    del temp_params["model"]
                
                params.update(temp_params)
                
                # Filter out parameters known to cause 400 errors on Gemini
                unsupported_params = ["frequency_penalty", "presence_penalty"]
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
            return result
        except Exception as e:
            raise Exception(f"Error calling Gemini API: {str(e)}")
            
    def get_model_info(self) -> Dict[str, Any]:
        return {
            "name": "Google Gemini",
            "provider": "Gemini",
            "type": "chat",
            "description": "Google Gemini models via OpenAI compatibility"
        }