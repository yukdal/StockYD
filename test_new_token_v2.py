import asyncio
import aiohttp

token = "8833596514:AAEX0o2lLeKc0k1x1OGtgtOxgf5vZLzzm7E"

async def main():
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with session.get(url) as response:
            data = await response.json()
            print(data)

asyncio.run(main())
