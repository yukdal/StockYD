import aiohttp
import os

class TelegramNotifier:
    def __init__(self, token=None, chat_id=None):
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send_message(self, text, session):
        """텔레그램 메시지 전송"""
        if not self.token or not self.chat_id:
            missing = []
            if not self.token: missing.append("TELEGRAM_BOT_TOKEN")
            if not self.chat_id: missing.append("TELEGRAM_CHAT_ID")
            print(f"⚠️ 텔레그램 설정 누락: {', '.join(missing)}")
            print(f"DEBUG (전송 시도한 메시지): \n{text}")
            return False
            
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        
        try:
            async with session.post(self.api_url, json=payload) as response:
                if response.status == 200:
                    return True
                else:
                    err_text = await response.text()
                    print(f"Telegram Error: {response.status} - {err_text}")
                    return False
        except Exception as e:
            print(f"Telegram Exception: {e}")
            return False
