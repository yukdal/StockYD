import asyncio
import aiohttp

token = "8833596514:AAEXlpaPZq702lk0Wzt1H0a8vccnRVxcg5c"

async def main():
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        async with session.get(url) as response:
            data = await response.json()
            print(data)

asyncio.run(main())
