import asyncio
import aiohttp

tokens_to_try = [
    "8833596514:AAEHRQG3sR8ZT7DPlC2pIXktCZK_fb-0NbQ",
    "8833596514:AAEHRQG3sR8ZT7DPlC2plXktCZK_fb-0NbQ",
    "8833596514:AAEHRQG3sR8ZT7DPlC2p1XktCZK_fb-0NbQ",
    "8833596514:AAEHRQG3sR8ZT7DPIc2plXktCZK_fb-0NbQ",
    "8833596514:AAEHRQG3sR8ZT7DPlc2plXktCZK_fb-0NbQ",
    "8833596514:AAEHRQG3sR8ZT7DPlc2pIXktCZK_fb-0NbQ",
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
