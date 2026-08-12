import aiohttp
import asyncio
import os

class TelegramNotifier:
    # 전송 실패 시 재시도 정책
    # 공시 알림은 놓치면 끝이므로 일시적 장애(네트워크 순단, 429, 5xx)는 반드시 재시도한다.
    MAX_SEND_ATTEMPTS = 3          # 채팅방당 최대 시도 횟수
    RETRY_BACKOFF = (1, 3)         # 재시도 전 대기 시간(초). 1차 실패 후 1초, 2차 실패 후 3초
    MAX_RETRY_AFTER = 60           # 텔레그램이 요구한 대기 시간이 이보다 길면 즉시 포기하고 상위 루프에 맡김

    # 재시도해도 결과가 달라지지 않는 상태 코드
    #   400: 잘못된 요청(채팅방 없음, 메시지 형식 오류 등)
    #   403: 봇이 차단되었거나 채팅방에서 추방됨
    #   404: 존재하지 않는 채팅방
    # 이 경우 계속 재시도하면 정상 채팅방에 중복 발송만 유발하므로 '영구 실패'로 처리한다.
    PERMANENT_ERROR_STATUSES = frozenset({400, 403, 404})

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
                                    
                                    # 새 채팅방 감지 시 즉시 등록 완료 안내 메시지 전송
                                    welcome_msg = f"✅ <b>[시스템 알림]</b>\n이 채팅방(<b>{chat_title}</b>)이 주식선물 실시간 공시 알림방으로 성공적으로 등록되었습니다.\n(앞으로 새로운 공시가 발생하면 즉시 알림이 발송됩니다.)"
                                    await self._send_to_single_chat(chat_id, welcome_msg, session)
                                    
                        if new_detected:
                            # .env 파일 업데이트 및 영구 저장
                            self._update_env_file()
        except Exception as e:
            print(f"⚠️ Telegram 자동 감지 오류: {e}")

    async def _send_once(self, chat_id, text, session):
        """단일 채팅방으로 1회 전송 시도.

        반환: (결과, 대기시간)
          결과 'ok'        — 전송 성공
          결과 'retry'     — 일시적 실패. 대기시간만큼 쉬었다가 재시도할 가치가 있음
          결과 'permanent' — 재시도해도 소용없는 실패 (채팅방 없음, 봇 차단 등)
        """
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        try:
            async with session.post(self.api_url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status == 200:
                    return 'ok', 0

                err_text = await response.text()

                # 429 Too Many Requests: 텔레그램이 알려준 대기 시간을 그대로 지킨다.
                if response.status == 429:
                    retry_after = self.RETRY_BACKOFF[0]
                    try:
                        body = await response.json()
                        retry_after = int(body.get('parameters', {}).get('retry_after', retry_after))
                    except Exception:
                        pass  # 본문 파싱 실패 시 기본 대기 시간 사용

                    if retry_after > self.MAX_RETRY_AFTER:
                        # 너무 길게 기다리면 감시 루프가 멈추므로 상위 루프의 다음 주기에 맡긴다.
                        print(f"⚠️ 텔레그램 요청 제한 (chat_id: {chat_id}): {retry_after}초 대기 요구 — 다음 주기에 재시도")
                        return 'retry', 0
                    print(f"⚠️ 텔레그램 요청 제한 (chat_id: {chat_id}): {retry_after}초 후 재시도")
                    return 'retry', retry_after

                if response.status in self.PERMANENT_ERROR_STATUSES:
                    print(f"❌ 전송 불가 (chat_id: {chat_id}): {response.status} - {err_text}")
                    print("   재시도해도 해결되지 않는 오류입니다. 채팅방 상태나 봇 권한을 확인하세요.")
                    return 'permanent', 0

                # 그 외(5xx 등)는 서버 측 일시 장애로 보고 재시도
                print(f"⚠️ 전송 실패 (chat_id: {chat_id}): {response.status} - {err_text}")
                return 'retry', self.RETRY_BACKOFF[0]

        except asyncio.TimeoutError:
            print(f"⚠️ 전송 시간 초과 (chat_id: {chat_id})")
            return 'retry', self.RETRY_BACKOFF[0]
        except aiohttp.ClientError as e:
            print(f"⚠️ 전송 중 통신 오류 (chat_id: {chat_id}): {e}")
            return 'retry', self.RETRY_BACKOFF[0]

    async def _send_with_retry(self, chat_id, text, session):
        """단일 채팅방으로 재시도를 포함해 전송.

        반환: 'ok' | 'permanent' | 'failed'
          'failed'는 재시도할 가치가 있었으나 횟수를 모두 소진한 경우로,
          호출한 쪽이 이 공시를 '전송 완료'로 기록하지 않아야 한다.
        """
        for attempt in range(1, self.MAX_SEND_ATTEMPTS + 1):
            result, wait = await self._send_once(chat_id, text, session)

            if result in ('ok', 'permanent'):
                return result

            if attempt < self.MAX_SEND_ATTEMPTS:
                # 텔레그램이 지정한 대기 시간이 있으면 그것을, 없으면 단계별 백오프를 사용
                delay = wait or self.RETRY_BACKOFF[min(attempt - 1, len(self.RETRY_BACKOFF) - 1)]
                print(f"   재시도 {attempt + 1}/{self.MAX_SEND_ATTEMPTS} ({delay}초 후)...")
                await asyncio.sleep(delay)

        print(f"❌ {self.MAX_SEND_ATTEMPTS}회 시도 후에도 전송하지 못했습니다 (chat_id: {chat_id})")
        return 'failed'

    async def _send_to_single_chat(self, chat_id, text, session):
        """단일 채팅방으로 메시지 전송 헬퍼 (환영 메시지 등)"""
        if not self.token:
            return False
        return await self._send_with_retry(chat_id, text, session) == 'ok'

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
        """텔레그램 메시지 전송 (실패 시 재시도 포함)

        반환값 True는 '이 메시지를 전송 완료로 기록해도 된다'는 뜻이다.
        재시도로도 해결되지 않은 일시적 실패가 하나라도 있으면 False를 반환하여,
        호출한 쪽이 해당 공시를 다음 주기에 다시 시도할 수 있게 한다.

        채팅방 하나가 '영구 실패'(봇 차단 등)인 경우는 True로 본다.
        재시도해도 달라지지 않으며, 계속 재시도하면 정상 채팅방에 중복 발송만
        반복되기 때문이다.
        """
        if not self.token or not self.chat_ids:
            missing = []
            if not self.token: missing.append("TELEGRAM_BOT_TOKEN")
            if not self.chat_ids: missing.append("TELEGRAM_CHAT_ID")
            print(f"⚠️ 텔레그램 설정 누락: {', '.join(missing)}")
            print(f"DEBUG (전송 시도한 메시지): \n{text}")
            return False

        retryable_failure = False
        # 전송 직전에 한 번 더 중복을 철저히 제거
        unique_chat_ids = list(dict.fromkeys(self.chat_ids))
        for chat_id in unique_chat_ids:
            if await self._send_with_retry(chat_id, text, session) == 'failed':
                retryable_failure = True

        return not retryable_failure
