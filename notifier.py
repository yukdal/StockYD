import aiohttp
import os

class TelegramNotifier:
    def __init__(self, token=None, chat_id=None):
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN')
        raw_chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        
        self.chat_ids = []
        if raw_chat_id:
            if isinstance(raw_chat_id, str):
                self.chat_ids = [c.strip() for c in raw_chat_id.split(',') if c.strip()]
            elif isinstance(raw_chat_id, list):
                self.chat_ids = raw_chat_id
            else:
                self.chat_ids = [str(raw_chat_id)]

    async def send_message(self, text, session):
        """텔레그램 메시지 전송"""
        if not self.token or not self.chat_ids:
            missing = []
            if not self.token: missing.append("TELEGRAM_BOT_TOKEN")
            if not self.chat_ids: missing.append("TELEGRAM_CHAT_ID")
            print(f"⚠️ 텔레그램 설정 누락: {', '.join(missing)}")
            print(f"DEBUG (전송 시도한 메시지): \n{text}")
            return False
            
        success_all = True
        for chat_id in self.chat_ids:
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            }
            
            try:
                async with session.post(self.api_url, json=payload) as response:
                    if response.status != 200:
                        err_text = await response.text()
                        print(f"Telegram Error (chat_id: {chat_id}): {response.status} - {err_text}")
                        success_all = False
            except Exception as e:
                print(f"Telegram Exception (chat_id: {chat_id}): {e}")
                success_all = False
                
        return success_all
