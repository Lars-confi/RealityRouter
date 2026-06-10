import asyncio
from src.router.core import get_agent_card

async def main():
    print(await get_agent_card())

asyncio.run(main())
