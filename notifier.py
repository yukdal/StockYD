import aiohttp
import os

class TelegramNotifier:
    def __init__(self, token=None, chat_id=None):
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN')
        raw_chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        self.offset = None
        
        self.chat_ids = []
        if raw_chat_id:
            if isinstance(raw_chat_id, str):
                self.chat_ids = [c.strip().strip("'").strip('"') for c in raw_chat_id.split(',') if c.strip()]
            elif isinstance(raw_chat_id, list):
                self.chat_ids = [str(c).strip().strip("'").strip('"') for c in raw_chat_id]
            else:
                self.chat_ids = [str(raw_chat_id).strip().strip("'").strip('"')]
                
        # 중복 제거 (순서 유지)
        self.chat_ids = list(dict.fromkeys(self.chat_ids))

    async def auto_detect_chat_ids(self, session):
        """텔레그램 getUpdates API를 사용하여 새로운 채팅방 ID를 자동 감지 및 등록"""
        if not self.token:
            return
            
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {}
        if self.offset is not None:
            params['offset'] = self.offset
            
        try:
            async with session.get(url, params=params, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('ok') and data.get('result'):
                        updates = data['result']
                        
                        # 다음 요청을 위한 offset 업데이트
                        self.offset = max(u['update_id'] for u in updates) + 1
                        
                        new_detected = False
                        for update in updates:
                            # 업데이트 내부의 모든 chat 객체 재귀 탐색
                            chats = self._find_chats(update)
                            
                            for chat in chats:
                                chat_id = str(chat['id'])
                                chat_title = chat.get('title') or chat.get('username') or chat.get('first_name') or "이름 없음"
                                chat_type = chat.get('type', 'unknown')
                                
                                if chat_id not in self.chat_ids:
                                    self.chat_ids.append(chat_id)
                                    new_detected = True
                                    print(f"✨ [Telegram] 새로운 채팅방 감지 및 등록: {chat_title} ({chat_type}, ID: {chat_id})")
                                    
                        if new_detected:
                            # .env 파일 업데이트 및 영구 저장
                            self._update_env_file()
        except Exception as e:
            print(f"⚠️ Telegram 자동 감지 오류: {e}")

    def _find_chats(self, data):
        """업데이트 데이터 내 모든 'chat' 객체를 재귀적으로 탐색하여 리스트로 반환"""
        chats = []
        if isinstance(data, dict):
            if 'chat' in data and isinstance(data['chat'], dict) and 'id' in data['chat']:
                chats.append(data['chat'])
            for value in data.values():
                chats.extend(self._find_chats(value))
        elif isinstance(data, list):
            for item in data:
                chats.extend(self._find_chats(item))
        return chats

    def _update_env_file(self):
        """현재 self.chat_ids 리스트를 .env 파일에 자동 업데이트 및 영구 저장"""
        env_path = '.env'
        joined_ids = ", ".join(self.chat_ids)
        
        try:
            if not os.path.exists(env_path):
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.write(f"TELEGRAM_CHAT_ID={joined_ids}\n")
                return

            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            replaced = False
            new_lines = []
            for line in lines:
                if line.strip().startswith("TELEGRAM_CHAT_ID="):
                    if not replaced:
                        new_lines.append(f"TELEGRAM_CHAT_ID={joined_ids}\n")
                        replaced = True
                    # replaced가 True이면 이미 썼으므로 무시 (중복 키 방지)
                else:
                    new_lines.append(line)

            if not replaced:
                if new_lines and not new_lines[-1].endswith('\n'):
                    new_lines[-1] += '\n'
                new_lines.append(f"TELEGRAM_CHAT_ID={joined_ids}\n")

            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                
            print(f"💾 .env 파일의 TELEGRAM_CHAT_ID가 업데이트되었습니다: {joined_ids}")
        except Exception as e:
            print(f"⚠️ .env 파일 업데이트 중 오류 발생: {e}")

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
        # 전송 직전에 한 번 더 중복을 철저히 제거
        unique_chat_ids = list(dict.fromkeys(self.chat_ids))
        for chat_id in unique_chat_ids:
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
