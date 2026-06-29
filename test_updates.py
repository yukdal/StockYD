import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('TELEGRAM_BOT_TOKEN')

async def main():
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        async with session.get(url) as response:
            data = await response.json()
            if data.get('ok') and data.get('result'):
                for update in data['result']:
                    if 'message' in update and 'chat' in update['message']:
                        chat = update['message']['chat']
                        print(f"Chat ID: {chat['id']}, Title/Name: {chat.get('title') or chat.get('first_name')}")
            else:
                print("No updates found.")

asyncio.run(main())
