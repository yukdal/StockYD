import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('TELEGRAM_BOT_TOKEN')

async def main():
    print("텔레그램 메시지 대기 중... (30초 동안 초고속 감지)")
    async with aiohttp.ClientSession() as session:
        for _ in range(1000):  # 5분 동안 1000번 확인 (0.3초 간격)
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            try:
                async with session.get(url, timeout=2) as response:
                    data = await response.json()
                    if data.get('ok') and data.get('result'):
                        for update in data['result']:
                            if 'message' in update and 'chat' in update['message']:
                                chat = update['message']['chat']
                                chat_id = str(chat['id'])
                                
                                # 올바른 채팅방을 찾았으니 바로 테스트 메시지 발송!
                                send_url = f"https://api.telegram.org/bot{token}/sendMessage"
                                payload = {
                                    "chat_id": chat_id,
                                    "text": "🎯 [테스트 완료]\n\n오라클 서버보다 빠르게 메시지를 낚아채서 이쪽 방이 맞는지 확인하고 테스트 알림을 발송했습니다!\n\n이제 이 방으로 오라클 서버가 앞으로 정상적으로 알림을 보낼 것입니다!"
                                }
                                await session.post(send_url, json=payload)
                                print(f"SUCCESS: {chat_id} 방으로 테스트 메시지를 발송했습니다!")
                                return
            except Exception as e:
                pass
            await asyncio.sleep(0.3)
    print("메시지를 잡지 못했습니다.")

asyncio.run(main())
