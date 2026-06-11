import asyncio
import aiohttp
import os
from scraper import DisclosureScraper
from logic import DisclosureLogic
from formatter import DisclosureFormatter
from notifier import TelegramNotifier
from dotenv import load_dotenv

import sys
import traceback
import socket
import os
import signal
import time

# .env 파일 로드 (로컬 환경용)
load_dotenv()

# 기존 좀비 봇 감지 및 자동 종료 로직
zombie_was_killed = False
pid_file = "bot.pid"
current_pid = os.getpid()

if os.path.exists(pid_file):
    try:
        with open(pid_file, 'r') as f:
            old_pid = int(f.read().strip())
        
        if old_pid != current_pid:
            try:
                os.kill(old_pid, signal.SIGTERM)
                time.sleep(1)
                zombie_was_killed = True
                print(f"🔫 기존 좀비 봇(PID: {old_pid})을 성공적으로 자동 종료했습니다.")
            except OSError:
                pass # 프로세스가 이미 없거나 종료할 권한이 없음
    except Exception:
        pass

# 현재 내 PID 저장
try:
    with open(pid_file, 'w') as f:
        f.write(str(current_pid))
except Exception:
    pass

# 단일 실행 잠금 (중복 봇 방지)
try:
    instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    instance_lock.bind(('127.0.0.1', 49123)) # 임의의 포트 사용
except socket.error:
    print("❌ 이미 실행 중인 봇 프로세스가 있습니다! 좀비 봇이 존재합니다.")
    print("❌ 실행을 중단합니다. 기존 프로세스를 먼저 완전히 종료해주세요.")
    sys.exit(1)

# 윈도우 터미널 인코딩 문제 해결 (UTF-8 강제 설정)
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Python 3.7 미만 버전 대응 (필요시)
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def run_monitor():
    import socket
    hostname = socket.gethostname()
    print(f"🚀 실시간 주식선물 공시 모니터링 시작... (Host: {hostname})")
    
    scraper = DisclosureScraper()
    logic = DisclosureLogic()
    notifier = TelegramNotifier()
    
    async with aiohttp.ClientSession() as session:
        # 프로그램 시작 시 1회 즉시 감지
        print("🔍 텔레그램 새 채팅방 감지 중...")
        await notifier.auto_detect_chat_ids(session)
        
        if zombie_was_killed:
            msg = "🔫 <b>[시스템 알림]</b>\n새로운 봇이 실행되면서 기존에 켜져 있던 봇(좀비 봇)을 감지하고 자동으로 종료했습니다.\n(이제 알림이 중복으로 오지 않습니다.)"
            await notifier.send_message(msg, session)
        
        while True:
            try:
                # 매 루프 시작 시 새로운 채팅방 감지 및 등록
                await notifier.auto_detect_chat_ids(session)
                
                # 1. 데이터 수집 (병렬 처리)
                kind_task = scraper.fetch_kind(session)
                dart_task = scraper.fetch_dart(session)
                
                results = await asyncio.gather(kind_task, dart_task)
                all_disclosures = results[0] + results[1]
                
                # 2. 필터링 및 우선순위 정렬
                filtered = logic.filter_disclosures(all_disclosures)
                
                # 3. 알림 전송
                if logic.is_first_ever_run:
                    if filtered:
                        print(f"⚠️ 최초 실행: {len(filtered)}개의 기존 공시 알림을 생략합니다 (텔레그램 스팸 제한 방지).")
                        logic.is_first_ever_run = False
                else:
                    for disc in filtered:
                        message = DisclosureFormatter.format_telegram_message(disc)
                        success = await notifier.send_message(message, session)
                        if success:
                            # 콘솔 로그 색상 적용 (상승: 빨강, 하락: 파랑)
                            color = "\033[91m" if disc['direction'] == "상승" else "\033[94m" if disc['direction'] == "하락" else "\033[0m"
                            reset = "\033[0m"
                            print(f"✅ {color}알림 전송 성공: {disc['corp_name']} ({disc['phase']}단계 {disc['direction']}){reset}")
                        
                        # 봇 차단 방지를 위한 미세 지연
                        await asyncio.sleep(0.5)
                
                # 4. 폴링 주기 지연 (3~5초)
                await asyncio.sleep(3)
                
            except Exception as e:
                print(f"❌ 루프 오류 발생: {e}")
                traceback.print_exc() # 상세 오류 정보 출력
                await asyncio.sleep(10) # 오류 시 잠시 대기 후 재시도

if __name__ == "__main__":
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n⏹ 모니터링 종료")
