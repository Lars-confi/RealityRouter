import aiohttp
import os
import openai
from typing import List, Dict, Any
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

async def discover_ollama_models(base_url: str = "http://localhost:11434") -> List[Dict[str, Any]]:
    """Discover models available on a local Ollama instance."""
    discovered = []
    try:
        url = f"{base_url}/api/tags"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    for model in data.get("models", []):
                        name = model.get("name")
                        if name:
                            # Use v1 endpoints as standard for openai compat
                            discovered.append({
                                "id": f"ollama_{name.replace(':', '-')}",
                                "name": f"Ollama {name}",
                                "provider": "generic",
                                "base_url": f"{base_url}/v1",
                                "model": name,
                                "api_key": "dummy"
                            })
    except Exception as e:
        logger.debug(f"Could not discover Ollama models at {base_url}: {e}")
    return discovered

async def discover_openai_models(api_key: str) -> List[Dict[str, Any]]:
    """Discover models available on OpenAI API."""
    if not api_key: return []
    discovered = []
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        models = await client.models.list()
        for model in models.data:
            if "gpt" in model.id:
                discovered.append({
                    "id": f"openai_{model.id.replace('.', '-')}",
                    "name": f"OpenAI {model.id}",
                    "provider": "openai",
                    "model": model.id
                })
    except Exception as e:
        logger.debug(f"Could not discover OpenAI models: {e}")
    return discovered

async def discover_gemini_models(api_key: str) -> List[Dict[str, Any]]:
    """Discover models available on Gemini via OpenAI compat."""
    if not api_key: return []
    discovered = []
    try:
        client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        models = await client.models.list()
        for model in models.data:
            discovered.append({
                "id": f"gemini_{model.id.replace('.', '-')}",
                "name": f"Google {model.id}",
                "provider": "gemini",
                "model": model.id
            })
    except Exception as e:
        logger.debug(f"Could not discover Gemini models: {e}")
    return discovered

