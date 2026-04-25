import asyncio
import httpx
import json

async def main():
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST", 
                "http://localhost:8000/v1/chat/completions",
                json={
                    "model": "qwen3-coder:30b",
                    "messages": [{"role": "user", "content": "Write a long story."}],
                    "stream": True
                },
                timeout=5.0
            ) as response:
                print(f"Status: {response.status_code}")
                async for chunk in response.aiter_text():
                    print(f"Chunk: {repr(chunk)}")
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(main())
