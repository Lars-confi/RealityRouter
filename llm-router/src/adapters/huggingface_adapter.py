"""
Hugging Face adapter for LLM routing system
"""
import os
import aiohttp
from typing import Dict, Any
from src.adapters.base_adapter import BaseAdapter
from src.models.routing import RoutingRequest

class HuggingFaceAdapter(BaseAdapter):
    """Adapter for Hugging Face Inference API"""
    
    def __init__(self, api_key: str = None):
        """
        Initialize the HF adapter
        """
        super().__init__("HuggingFace", api_key)
        
        if not api_key:
            api_key = os.getenv("HUGGINGFACE_API_KEY")
        
        if not api_key:
            raise ValueError("Hugging Face API key is required")
        
        self.api_key = api_key
        self.default_model = "mistralai/Mistral-7B-Instruct-v0.2"
    
    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        try:
            model = self.default_model
            if request.parameters and "model" in request.parameters and "huggingface" in request.parameters["model"]:
                model = request.parameters["model"].replace("huggingface_", "")
                
            api_url = f"https://api-inference.huggingface.co/models/{model}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            payload = {
                "inputs": request.query,
                "parameters": {
                    "max_new_tokens": request.parameters.get("max_tokens", 1000) if request.parameters else 1000,
                    "temperature": request.parameters.get("temperature", 0.7) if request.parameters else 0.7,
                    "return_full_text": False
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"HF API Error {response.status}: {error_text}")
                    
                    result = await response.json()
                    
                    # Handle both list and dict returns depending on the model
                    text = ""
                    if isinstance(result, list) and len(result) > 0:
                        text = result[0].get("generated_text", "")
                    elif isinstance(result, dict):
                        text = result.get("generated_text", "")
                        
                    return {
                        "text": text,
                        "usage": {
                            "prompt_tokens": len(request.query) // 4, # rough estimate
                            "completion_tokens": len(text) // 4,      # rough estimate
                            "total_tokens": (len(request.query) + len(text)) // 4
                        },
                        "model": model,
                        "finish_reason": "stop"
                    }
                    
        except Exception as e:
            raise Exception(f"Error calling Hugging Face API: {str(e)}")
    
    def get_model_info(self) -> Dict[str, Any]:
        return {
            "name": "Hugging Face Inference",
            "provider": "HuggingFace",
            "type": "chat",
            "description": "Hugging Face inference endpoints"
        }