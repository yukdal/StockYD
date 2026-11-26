import asyncio
import aiohttp
import os
from scraper import DisclosureScraper
from logic import DisclosureLogic
from formatter import DisclosureFormatter
from notifier import TelegramNotifier
from krx_api import KRXOpenAPI  # KRX 공식 Open API 모듈 (전일 주식선물 매매정보 보강용)
from kis_api import KISOpenAPI  # 한국투자증권 Open API (KIND 대신 공시 제목 수집)
from scrape_watchdog import ScrapeWatchdog, build_warning_message, build_recovery_message
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

# 알림 전송에 실패했을 때 다음 감시 주기까지 쉬는 시간(초).
# 실패 직후 3초 만에 다시 시도하면 같은 이유로 실패하며 텔레그램 API만 두드리게 된다.
SEND_RETRY_COOLDOWN = 30

# 감시 주기(초).
#
# ⚠️ 예전에는 3초였고, 매 주기 세 시장을 모두 조회해 분당 약 40회를 장중 내내
# 보냈다. 결국 KRX가 서버 IP를 차단(HTTP 403)해 공시를 전혀 받지 못하는 사고가
# 났다. 지금은 30초 주기 + 시장 1개씩 순환이라 분당 약 2회로, 요청량이 20분의 1이다.
# (DART Open API의 일 10,000건 제한도 예전 주기로는 초과하고 있었다.)
POLL_INTERVAL = 30

# KIND 수집이 실패했을 때 점점 길게 쉬는 간격(초).
# 차단당한 상태에서 같은 주기로 계속 두드리면 차단이 연장되기만 한다.
FETCH_BACKOFF_STEPS = (60, 180, 600, 1800)

# 백오프를 해제하려면 연속으로 이만큼 성공해야 한다.
# 차단 중에도 200이 간헐적으로 섞여 나오는 것이 실제로 관측되었다. 한 번의 200에
# 백오프를 0으로 되돌리면 200/403이 번갈아 올 때 최장 단계(30분)에 영영 닿지 못한다.
BACKOFF_RESET_SUCCESSES = 2

# 백오프 상태 저장 파일. 재시작해도 백오프가 처음부터 다시 시작되지 않도록 한다.
# (예전에는 메모리에만 있어서 deploy.sh나 재시작 한 번에 0으로 돌아가, 차단 중에도
#  기동 직후 곧바로 KIND를 다시 두드렸다)
BACKOFF_STATE_FILE = ".kind_backoff_state"
BACKOFF_STATE_MAX_AGE = 6 * 60 * 60  # 이보다 오래된 상태는 무시 (밤새 쉬었으면 새로 시작)


def _load_backoff_state():
    """저장된 백오프 상태를 읽는다. 반환: (연속 실패 횟수, 다음 요청 허용 시각)"""
    try:
        if time.time() - os.path.getmtime(BACKOFF_STATE_FILE) > BACKOFF_STATE_MAX_AGE:
            return 0, 0.0
        with open(BACKOFF_STATE_FILE, 'r') as f:
            failures, next_allowed = f.read().split()
        return int(failures), float(next_allowed)
    except (OSError, ValueError):
        return 0, 0.0


def _save_backoff_state(failures, next_allowed):
    """백오프 상태를 저장한다. 정상 상태면 파일을 지운다."""
    try:
        if failures <= 0:
            if os.path.exists(BACKOFF_STATE_FILE):
                os.remove(BACKOFF_STATE_FILE)
            return
        with open(BACKOFF_STATE_FILE, 'w') as f:
            f.write(f"{failures} {next_allowed:.0f}")
    except OSError:
        pass  # 저장 실패가 감시 자체를 멈추게 해서는 안 된다


def _backoff_delay(failures):
    """연속 실패 횟수에 해당하는 대기 시간(초)"""
    return FETCH_BACKOFF_STEPS[min(failures - 1, len(FETCH_BACKOFF_STEPS) - 1)]


# 같은 공시가 이 횟수만큼 연속으로 처리 중 예외를 내면 건너뛴다.
# (전송 실패한 공시는 기록하지 않아 매 주기 다시 처리되므로, 예외를 내는 공시가 있으면
#  매 주기 같은 예외가 재현되고 그 뒤에 정렬된 알림까지 막힌다)
DISC_ERROR_LIMIT = 3


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
    kind_watchdog = ScrapeWatchdog()  # 공시 수집이 조용히 고장난 상태를 감시

    # 공시 수집원 선택
    # KRX가 클라우드(OCI) IP 대역의 KIND 요청을 403으로 막기 때문에, KIS 키가 설정되어 있으면
    # KIS Open API를 주 수집원으로 쓰고 KIND는 호출하지 않는다 (막힌 곳을 두드리지 않도록).
    kis = KISOpenAPI()
    use_kis = kis.is_configured
    source_label = "KIS Open API" if use_kis else "KIND"
    print(f"📡 공시 수집원: {source_label}"
          + (f" (제목 필터: '{kis.title_filter}')" if use_kis and kis.title_filter else ""))

    # KIND 수집 연속 실패 횟수와 다음 요청 허용 시각 (재시작해도 이어받는다)
    fetch_failures, next_kind_allowed_at = _load_backoff_state()
    if fetch_failures:
        wait = max(0, int(next_kind_allowed_at - time.time()))
        print(f"⏳ 이전 실행의 백오프를 이어받습니다: 연속 실패 {fetch_failures}회, {wait}초 후 KIND 요청 재개")
    fetch_successes = 0               # 백오프 중 연속 성공 횟수 (BACKOFF_RESET_SUCCESSES 도달 시 해제)
    loop_errors = 0                   # 메인 루프 연속 예외 횟수
    disc_errors = {}                  # 공시별 연속 예외 횟수 {해시: 횟수}

    # 기동 직후 워밍업 주기 수. 이 동안에는 이미 지나간 공시를 발송하지 않고 기록만 한다.
    # 시장을 순환 조회하므로 '시장 수'만큼(= 한 바퀴) 유지해야 모든 시장의 기존 공시가
    # 뒤늦게 발송되는 것을 막을 수 있다.
    warmup_cycles = len(DisclosureScraper.MARKET_TYPES)
    
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

                # 백오프 중이면 허용 시각까지 KIND를 요청하지 않는다.
                # (재시작으로 이어받은 백오프도 여기서 지켜진다. 60초씩 끊어 자서 채팅방 감지·장 시간 판정은 계속 돈다)
                remaining = next_kind_allowed_at - time.time()
                if remaining > 0:
                    await asyncio.sleep(min(remaining, 60))
                    continue

                # 1. 데이터 수집 (병렬 처리)
                primary_task = kis.fetch_disclosures(session) if use_kis else scraper.fetch_kind(session)
                dart_task = scraper.fetch_dart(session)

                results = await asyncio.gather(primary_task, dart_task)
                all_disclosures = results[0] + results[1]

                # 1-1. 수집기 상태 감시
                # KIND는 HTML 파싱이라 페이지 구조가 바뀌면 예외 없이 빈 목록만 돌려준다.
                # 그 상태로 두면 봇은 살아 있는데 아무것도 탐지하지 못하므로, 이상이
                # 일정 시간 지속되면 알림방으로 알린다.
                kind_stats = kis.last_stats if use_kis else scraper.last_kind_stats
                # 시장을 순환 조회하므로 이번 주기의 건수만 보면 안 된다.
                # (해당 시장에 공시가 없는 시간대에 '0건'으로 오탐이 난다)
                # 시장별 최근값을 합친 rows_recent를 기준으로 판단한다.
                watch_signal = kind_watchdog.record(kind_stats['rows_recent'], kind_stats['http_ok'])
                if watch_signal:
                    action, detail = watch_signal
                    if action == 'warn':
                        minutes = kind_watchdog.elapsed_minutes()
                        print(f"⚠️ 수집 이상 감지({detail}): {minutes}분째 지속 — 경고 알림을 보냅니다.")
                        await notifier.send_message(
                            build_warning_message(detail, minutes, kind_stats.get('last_status'), source_label), session)
                    elif action == 'recover':
                        print(f"✅ 수집이 정상으로 돌아왔습니다 ({detail}건 수신).")
                        await notifier.send_message(build_recovery_message(detail, source_label), session)

                # 수집 실패가 이어지면 점점 길게 쉰다 (차단 상태에서 계속 두드리지 않도록)
                if kind_stats['http_ok']:
                    if fetch_failures:
                        fetch_successes += 1
                        if fetch_successes >= BACKOFF_RESET_SUCCESSES:
                            print(f"✅ KIND 수집이 {fetch_successes}회 연속 성공해 백오프를 해제합니다.")
                            fetch_failures = 0
                            fetch_successes = 0
                        # 아직 연속 성공이 부족하면 백오프 단계를 유지한다 (간헐적 200 대비)
                else:
                    fetch_failures += 1
                    fetch_successes = 0
                next_kind_allowed_at = time.time() + _backoff_delay(fetch_failures) if fetch_failures else 0.0
                _save_backoff_state(fetch_failures, next_kind_allowed_at)

                # 2. 필터링 및 우선순위 정렬
                filtered = logic.filter_disclosures(all_disclosures)
                
                # 3. 알림 전송
                send_failed = False  # 이번 주기에 재시도가 필요한 실패가 있었는지

                if warmup_cycles > 0:
                    # 기동 직후 '워밍업' 구간.
                    # 이미 지나간 공시를 뒤늦게 무더기로 보내지 않도록, 보내지 않고 기록만 한다.
                    #
                    # ⚠️ 시장을 순환 조회하므로 한 주기만 생략해서는 부족하다.
                    # 첫 주기는 유가증권만 보므로, 코스닥·파생상품의 오늘 공시는
                    # 2~3주기에 '새 공시'로 잡혀 뒤늦게 발송된다.
                    # 그래서 시장 수만큼(한 바퀴) 워밍업을 유지한다.
                    warmup_cycles -= 1
                    market_label = ",".join(kind_stats.get('markets') or ())
                    if filtered:
                        print(f"⚠️ 기동 중(시장 {market_label}): 기존 공시 {len(filtered)}건의 알림을 생략합니다. "
                              f"(남은 워밍업 {warmup_cycles}주기)")
                        # 보내지는 않지만 '이미 처리한 공시'로 기록해 다음 주기에 다시 뜨지 않게 한다
                        for disc in filtered:
                            logic.mark_sent(disc)
                    else:
                        print(f"⚠️ 기동 중(시장 {market_label}): 기존 공시 중 새로운 항목이 없습니다. "
                              f"(남은 워밍업 {warmup_cycles}주기)")
                    logic.is_first_ever_run = False
                else:
                    for disc in filtered:
                        disc_key = logic.get_hash(disc)
                        # KRX 공식 API로 해당 종목의 전일 선물 매매정보 조회 (실패해도 알림은 그대로 전송)
                        krx_info = None
                        try:
                            krx_info = await krx.get_futures_summary(session, disc['corp_name'])
                        except Exception as e:
                            print(f"⚠️ KRX 전일 데이터 조회 실패 (알림은 정상 전송): {e}")

                        # 공시 하나에서 난 예외가 루프 전체(와 뒤에 정렬된 알림)를 막지 않도록 개별 처리
                        try:
                            message = DisclosureFormatter.format_telegram_message(disc, krx_info)
                            success = await notifier.send_message(message, session)
                            disc_errors.pop(disc_key, None)
                        except Exception as e:
                            count = disc_errors.get(disc_key, 0) + 1
                            disc_errors[disc_key] = count
                            print(f"❌ 공시 처리 중 오류 ({count}/{DISC_ERROR_LIMIT}): {disc.get('corp_name')} — "
                                  f"{type(e).__name__}: {e}")
                            if count >= DISC_ERROR_LIMIT:
                                print(f"   같은 오류가 {count}회 반복되어 이 공시는 건너뜁니다: {disc.get('title')}")
                                logic.mark_sent(disc)
                                disc_errors.pop(disc_key, None)
                            else:
                                send_failed = True
                            continue

                        if success:
                            # 전송에 성공한 뒤에야 '본 공시'로 기록한다.
                            # 기록 전에 실패하면 다음 주기에 다시 후보로 올라와 재시도된다.
                            logic.mark_sent(disc)
                            # 콘솔 로그 색상 적용 (상승: 빨강, 하락: 파랑)
                            color = "\033[91m" if disc['direction'] == "상승" else "\033[94m" if disc['direction'] == "하락" else "\033[0m"
                            reset = "\033[0m"
                            print(f"✅ {color}알림 전송 성공: {disc['corp_name']} ({disc['phase']}단계 {disc['direction']}){reset}")
                        else:
                            send_failed = True
                            print(f"🔁 전송 실패로 기록하지 않았습니다. 다음 주기에 다시 시도합니다: "
                                  f"{disc['corp_name']} ({disc['phase']}단계 {disc['direction']})")

                        # 봇 차단 방지를 위한 미세 지연
                        await asyncio.sleep(0.5)

                # 4. 다음 주기까지 대기
                loop_errors = 0  # 이번 주기는 예외 없이 끝났다

                if fetch_failures:
                    # KIND 수집 실패가 이어지는 중 — 차단(403)일 수 있으므로 점점 길게 쉰다.
                    delay = _backoff_delay(fetch_failures)
                    print(f"⏳ KIND 수집 실패 {fetch_failures}회 연속 — {delay}초 후 재시도합니다.")
                elif send_failed:
                    # 전송에 실패했다면 곧바로 다시 시도해봐야 같은 이유로 실패할 가능성이 크고,
                    # 텔레그램 API만 계속 두드리게 되므로 잠시 길게 쉬었다가 재시도한다.
                    delay = SEND_RETRY_COOLDOWN
                    print(f"⏳ 전송 실패가 있어 {delay}초 후 재시도합니다.")
                else:
                    delay = POLL_INTERVAL

                await asyncio.sleep(delay)
                
            except Exception as e:
                print(f"❌ 루프 오류 발생: {type(e).__name__}: {e}")
                traceback.print_exc() # 상세 오류 정보 출력
                # ⚠️ 예전에는 10초만 쉬고 재시도해 백오프를 우회했다. 매 주기 같은 예외가 나면
                # 차단 중인 KRX를 10초마다 두드리게 된다. 정상 주기보다 짧게 쉬지 않고,
                # 예외가 이어지면 백오프 단계처럼 점점 길게 쉰다.
                loop_errors += 1
                delay = max(POLL_INTERVAL, _backoff_delay(loop_errors),
                            _backoff_delay(fetch_failures) if fetch_failures else 0)
                print(f"⏳ 루프 오류 {loop_errors}회 연속 — {delay}초 후 재시도합니다.")
                await asyncio.sleep(delay)

if __name__ == "__main__":
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n⏹ 모니터링 종료")
