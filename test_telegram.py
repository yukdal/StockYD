"""텔레그램 봇 토큰 및 채팅방 확인 스크립트

토큰을 새로 발급받은 뒤 봇을 켜기 전에 정상 동작하는지 확인하는 용도입니다.
토큰은 .env의 TELEGRAM_BOT_TOKEN에서 읽으므로 이 파일에 직접 적지 마십시오.

사용법:
    python test_telegram.py
"""
import asyncio
import os
import sys

import aiohttp
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _mask(token):
    """로그에 토큰 전체가 남지 않도록 가립니다. (예: 8833596514:AAEH…)"""
    if ':' not in token:
        return token[:4] + "…"
    bot_id, secret = token.split(':', 1)
    return f"{bot_id}:{secret[:4]}…"


async def _call(session, token, method):
    """텔레그램 API 호출. 성공하면 응답 dict, 실패하면 None을 반환하고 이유를 출력합니다.

    진단용 스크립트이므로 네트워크 오류나 JSON이 아닌 응답에도 트레이스백 대신
    읽을 수 있는 메시지를 남깁니다.
    """
    url = API_BASE.format(token=token, method=method)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as res:
            try:
                return await res.json()
            except aiohttp.ContentTypeError:
                body = (await res.text())[:200]
                print(f"❌ {method}: 텔레그램이 JSON이 아닌 응답을 보냈습니다 (HTTP {res.status})")
                print(f"   응답 내용: {body}")
                print("👉 프록시나 방화벽이 api.telegram.org 요청을 가로채고 있을 수 있습니다.")
                return None
    except asyncio.TimeoutError:
        print(f"❌ {method}: 응답 시간이 초과되었습니다 (15초). 네트워크를 확인해주세요.")
        return None
    except aiohttp.ClientError as e:
        print(f"❌ {method}: 접속에 실패했습니다 — {e}")
        return None


async def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        print("❌ .env에 TELEGRAM_BOT_TOKEN이 없습니다.")
        print("👉 README의 '환경변수 설정'을 참고하여 .env를 먼저 만들어주세요.")
        return 1

    print(f"🔑 토큰: {_mask(token)}")

    async with aiohttp.ClientSession() as session:
        # 1. 토큰 유효성 확인
        data = await _call(session, token, 'getMe')
        if data is None:
            return 1

        if not data.get('ok'):
            print(f"❌ 토큰이 유효하지 않습니다: {data.get('description', data)}")
            print("👉 BotFather에서 토큰을 재발급받았다면 .env를 갱신했는지 확인해주세요.")
            return 1

        bot = data['result']
        print(f"✅ 토큰 정상 — 봇 이름: {bot.get('first_name')} (@{bot.get('username')})")

        # 2. 봇이 참여 중인 채팅방 확인 (.env의 TELEGRAM_CHAT_ID를 채울 때 사용)
        data = await _call(session, token, 'getUpdates')
        if data is None:
            return 1

        updates = data.get('result') or []
        if not updates:
            print("ℹ️ 최근 수신된 메시지가 없어 채팅방을 확인할 수 없습니다.")
            print("👉 알림방에 아무 메시지나 한 번 보낸 뒤 다시 실행해보세요.")
            return 0

        # 중복 제거하며 채팅방 목록 수집 (순서 유지)
        chats = {}
        for update in updates:
            for value in update.values():
                if isinstance(value, dict) and isinstance(value.get('chat'), dict):
                    chat = value['chat']
                    chats[str(chat['id'])] = chat

        print(f"\n💬 감지된 채팅방 {len(chats)}개:")
        for chat_id, chat in chats.items():
            name = chat.get('title') or chat.get('username') or chat.get('first_name') or '이름 없음'
            print(f"   {chat_id}  ({chat.get('type', 'unknown')})  {name}")
        print("\n👉 위 ID를 .env의 TELEGRAM_CHAT_ID에 쉼표로 구분해 넣으면 됩니다.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
