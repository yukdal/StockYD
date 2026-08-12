import asyncio
import aiohttp
import os
from scraper import DisclosureScraper
from logic import DisclosureLogic
from formatter import DisclosureFormatter
from notifier import TelegramNotifier
from krx_api import KRXOpenAPI  # KRX 공식 Open API 모듈 (전일 주식선물 매매정보 보강용)
from dotenv import load_dotenv

import sys
import traceback
import socket
import os
import signal
import time

# .env 파일 로드 (로컬 환경용)
load_dotenv()

# ---------------------------------------------------------------------------
# 단일 실행 보장 ('먼저 잡은 쪽이 이긴다')
#
# 예전에는 PID 파일을 읽어 기존 봇에 SIGTERM을 보내 자리를 빼앗는 방식이었다.
# 이 방식은 systemd(Restart=always)처럼 프로세스를 자동으로 되살리는 환경에서
# 두 인스턴스가 서로를 죽이고 되살아나는 무한 루프를 만든다.
#   A 실행 중 → B가 A를 죽임 → supervisor가 A를 되살림 → A가 B를 죽임 → 무한 반복
# (실제로 재시작마다 텔레그램 시작 알림이 발송되어 알림 폭탄이 발생했다.)
#
# 그래서 기본 동작을 '이미 실행 중이면 새 프로세스가 조용히 물러난다'로 바꾼다.
# 기존의 강제 인수 동작이 필요한 경우에만 STOCKYD_TAKEOVER=1 로 명시적으로 켠다.
# ---------------------------------------------------------------------------

# 이미 다른 봇이 실행 중이라 종료할 때 쓰는 종료 코드.
# stockyd.service의 RestartPreventExitStatus 값과 반드시 일치해야 한다
# (systemd가 이 코드로 끝난 서비스는 재시작하지 않도록).
EXIT_ALREADY_RUNNING = 3

LOCK_PORT = 51234  # 다른 OCI 봇과 겹치지 않는 고유 포트
pid_file = "stock_monitor.pid"
current_pid = os.getpid()
zombie_was_killed = False


def _acquire_instance_lock():
    """단일 실행 잠금 획득 시도. 성공하면 소켓 객체, 이미 점유 중이면 None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', LOCK_PORT))
        return sock
    except socket.error:
        sock.close()
        return None


instance_lock = _acquire_instance_lock()

# STOCKYD_TAKEOVER=1 인 경우에만 기존 봇을 종료하고 자리를 넘겨받는다.
# (수동으로 봇을 교체할 때만 사용. supervisor가 관리하는 환경에서는 절대 켜지 말 것.)
if instance_lock is None and os.getenv('STOCKYD_TAKEOVER') == '1':
    try:
        with open(pid_file, 'r') as f:
            old_pid = int(f.read().strip())
        if old_pid != current_pid:
            os.kill(old_pid, signal.SIGTERM)
            print(f"🔫 기존 봇(PID: {old_pid})에 종료 신호를 보냈습니다. 자리를 넘겨받는 중...")
            # 기존 봇이 잠금을 놓을 때까지 최대 10초 대기
            for _ in range(10):
                time.sleep(1)
                instance_lock = _acquire_instance_lock()
                if instance_lock is not None:
                    zombie_was_killed = True
                    break
    except (OSError, ValueError, FileNotFoundError):
        pass  # PID 파일이 없거나 이미 종료된 프로세스

if instance_lock is None:
    print("ℹ️ 이미 실행 중인 봇이 있어 이 프로세스는 종료합니다. (중복 실행 방지)")
    print("   기존 봇을 교체하려면 먼저 종료하거나 STOCKYD_TAKEOVER=1 로 실행하세요.")
    sys.exit(EXIT_ALREADY_RUNNING)

# 잠금을 확보한 뒤에만 PID 파일을 갱신 (실행 중인 봇의 PID만 기록되도록)
try:
    with open(pid_file, 'w') as f:
        f.write(str(current_pid))
except Exception:
    pass

# ---------------------------------------------------------------------------
# 시작 알림 도배 방지
# 봇이 크래시 루프에 빠지면 재시작마다 시작 알림이 나가 알림방이 도배된다.
# 최근에 이미 보냈다면 이번 시작 알림은 생략한다 (감시 기능 자체는 정상 동작).
# ---------------------------------------------------------------------------
STARTUP_NOTICE_FILE = ".last_startup_notice"
STARTUP_NOTICE_INTERVAL = 600  # 초 단위 (10분)


def _should_send_startup_notice():
    """직전 시작 알림으로부터 STARTUP_NOTICE_INTERVAL이 지났으면 True."""
    try:
        if time.time() - os.path.getmtime(STARTUP_NOTICE_FILE) < STARTUP_NOTICE_INTERVAL:
            return False
    except OSError:
        pass  # 파일이 없으면 첫 실행이므로 그대로 전송

    try:
        with open(STARTUP_NOTICE_FILE, 'w') as f:
            f.write(str(int(time.time())))
    except Exception:
        pass  # 기록에 실패해도 알림 전송은 막지 않는다
    return True


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
    krx = KRXOpenAPI()  # KRX 공식 API 객체 생성 (.env의 KRX_AUTH_KEY 자동 로드, 없으면 비활성)
    
    async with aiohttp.ClientSession() as session:
        # 프로그램 시작 시 1회 즉시 감지
        print("🔍 텔레그램 새 채팅방 감지 중...")
        await notifier.auto_detect_chat_ids(session)
        
        # KRX 공식 API 연동 상태 확인 (인증키가 설정된 경우에만)
        krx_status = "미설정 (알림은 정상 동작)"  # 기본 상태 문구
        if krx.is_configured:                      # 인증키가 있으면 실제 연결 테스트
            try:
                test_days = krx._recent_business_days(max_back=3)   # 최근 영업일 3개 후보
                test_rows = []                                       # 테스트 결과 담을 변수
                for d in test_days:                                  # 날짜별로 시도
                    test_rows = await krx.get_day_data(session, d)   # 데이터 조회
                    if test_rows:                                    # 받으면 중단
                        break
                krx_status = f"✅ 연동 성공 ({len(test_rows)}개 종목 수신)" if test_rows else "⚠️ 인증키는 있으나 데이터 수신 실패"
            except Exception as e:
                krx_status = f"⚠️ 연결 오류: {e}"
        print(f"📊 KRX 공식 API 상태: {krx_status}")

        # 봇 구동 시작 알림 전송
        # 봇이 반복 재시작(크래시 루프)에 빠지면 시작 알림이 그대로 알림 폭탄이 되므로,
        # 최근에 이미 보냈다면 이번 알림은 건너뛴다. 모니터링 동작 자체에는 영향이 없다.
        if _should_send_startup_notice():
            start_msg = f"🚀 <b>[시스템 알림]</b>\n주식선물 실시간 공시 모니터링 봇이 정상 작동을 시작했습니다.\n(서버 호스트: <code>{hostname}</code>)\n(KRX 공식 API: {krx_status})"
            await notifier.send_message(start_msg, session)

            if zombie_was_killed:
                msg = "🔫 <b>[시스템 알림]</b>\n새로운 봇이 실행되면서 기존에 켜져 있던 봇(좀비 봇)을 감지하고 자동으로 종료했습니다.\n(이제 알림이 중복으로 오지 않습니다.)"
                await notifier.send_message(msg, session)
        else:
            print(f"⏭ 최근 {STARTUP_NOTICE_INTERVAL // 60}분 이내에 시작 알림을 이미 보내 이번에는 생략합니다. "
                  f"(반복 재시작 시 알림 폭탄 방지)")
        
        while True:
            try:
                # 매 루프 시작 시 새로운 채팅방 감지 및 등록
                await notifier.auto_detect_chat_ids(session)
                
                # 주식장 열리는 시간(KST 기준 08:00 ~ 18:00) 여부 확인
                from datetime import datetime, timezone, timedelta
                kst = timezone(timedelta(hours=9))
                now_kst = datetime.now(kst)
                
                # 주말(토, 일) 또는 평일 08:00~18:00 이외의 시간은 장 마감 시간으로 판단하여 크롤링 중단
                if now_kst.weekday() >= 5 or not (8 <= now_kst.hour < 18):
                    await asyncio.sleep(30) # 장 마감 시간대에는 30초 대기 후 스킵 (서버 자원 절약 및 심야 오작동 방지)
                    continue
                
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
                    else:
                        print("⚠️ 최초 실행: 기존 공시 중 새로운 항목이 없습니다.")
                    logic.is_first_ever_run = False
                else:
                    for disc in filtered:
                        # KRX 공식 API로 해당 종목의 전일 선물 매매정보 조회 (실패해도 알림은 그대로 전송)
                        krx_info = None
                        try:
                            krx_info = await krx.get_futures_summary(session, disc['corp_name'])
                        except Exception as e:
                            print(f"⚠️ KRX 전일 데이터 조회 실패 (알림은 정상 전송): {e}")
                        message = DisclosureFormatter.format_telegram_message(disc, krx_info)
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
