import asyncio
import aiohttp

tokens_to_try = [
    "8833596514:AAEXlpaPZq702lk0Wzt1H0a8vccnRVxcg5c",
    "8833596514:AAEXlpaPZq7O2IkOWzt1H0a8vccnRVxcg5c",
    "8833596514:AAEXlpaPZq7O2lkOWzt1H0a8vccnRVxcg5c",
    "8833596514:AAEXlpaPZq702Ik0Wzt1H0a8vccnRVxcg5c",
    "8833596514:AAEXIpaPZq7O2IkOWzt1H0a8vccnRVxcg5c",
    "8833596514:AAEX1paPZq7O2IkOWzt1H0a8vccnRVxcg5c",
    "8833596514:AAEXIpaPZq702Ik0Wzt1H0a8vccnRVxcg5c",
]

async def main():
    async with aiohttp.ClientSession() as session:
        for token in tokens_to_try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            async with session.get(url) as response:
                data = await response.json()
                if data.get("ok"):
                    print(f"VALID TOKEN FOUND: {token}")
                    print(data)
                    return
        print("NONE ARE VALID")

asyncio.run(main())
