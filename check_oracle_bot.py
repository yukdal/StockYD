import asyncio
import aiohttp

token = "8833596514:AAEHRQG3sR8ZT7DPlC2pIXktCZK_fb-0NbQ"

async def main():
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with session.get(url) as response:
            data = await response.json()
            print(data)

asyncio.run(main())
