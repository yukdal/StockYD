# kis_api.py
# 한국투자증권(KIS) Open API 연동 모듈 — 공시 제목 수집용
#
# 왜 필요한가:
#   KRX가 클라우드(OCI) IP 대역에서 오는 KIND 요청을 HTTP 403으로 막는다.
#   공인 IP를 새로 받아도 403이었으므로, IP를 바꾸는 방식으로는 해결되지 않는다.
#   KIS Open API는 KRX 서버를 거치지 않는 증권사 공식 API라 이 차단과 무관하다.
#
# 사용하는 API (공식 예제 koreainvestment/open-trading-api 기준):
#   - 토큰 발급:  POST /oauth2/tokenP
#   - 공시 제목:  GET  /uapi/domestic-stock/v1/quotations/news-title  (TR: FHKST01011800)
#
# ⚠️ 토큰은 반드시 파일에 저장해 재사용한다.
#   KIS는 토큰을 발급할 때마다 계좌 명의자에게 카카오 알림톡을 보낸다.
#   봇이 재시작될 때마다 새로 발급하면 휴대폰으로 알림이 계속 간다.
#   토큰 유효기간은 1일이다.
#
# 필요한 .env 값:
#   KIS_APP_KEY=...
#   KIS_APP_SECRET=...
#   KIS_DISCLOSURE_CODES=I        (선택. 조회할 제공업체코드, 쉼표로 여러 개. 기본 I=거래소 시장조치 공시)
#   실측(2026-11-26): I=공시(주식선물 가격제한폭 확대 공시가 여기로 옴), F=거래소 공시,
#   G=코스닥 공시, N=코넥스 공시, H=K-OTC 공시
#
# 수집 방식 (서버 실측 결과, 2026-11-26):
#   - 제목 검색(FID_TITL_CNTT)은 무시되고 항상 최신 40건이 온다 → 제목 필터는 쓰지 않고 로컬(logic.py)에서 거른다.
#   - 제공업체코드(FID_NEWS_OFER_ENTP_CODE) 필터는 동작한다 → 공시 계열 코드만 조회한다.
#   - FID_INPUT_DATE_1(00YYYYMMDD) + FID_INPUT_HOUR_1(0000HHMMSS)를 주면 그 시각 이전 40건이 온다.
#     응답 헤더 tr_cont는 비어 있어(연속조회 없음) 이 '시각 되감기'로 페이지를 넘긴다.
#   - 코드별로 마지막으로 본 지점(워터마크)을 기억하고, 최신 페이지만으로 워터마크까지 닿지 못하면
#     (30초 사이 40건 넘게 쏟아진 경우) 시각을 거슬러 올라가며 빠진 구간을 채운다.

import asyncio
import json
import os
import stat
import tempfile
import time
from datetime import datetime, timedelta, timezone

import aiohttp

KST = timezone(timedelta(hours=9))


class KISOpenAPI:
    BASE_URL = "https://openapi.koreainvestment.com:9443"   # 실전 도메인 (시세·공시 조회는 실전 키로)
    TOKEN_PATH = "/oauth2/tokenP"
    NEWS_TITLE_PATH = "/uapi/domestic-stock/v1/quotations/news-title"
    NEWS_TITLE_TR_ID = "FHKST01011800"

    TOKEN_CACHE_FILE = ".kis_token.json"   # 토큰 캐시 (알림톡 폭탄 방지). .gitignore 대상
    TOKEN_REFRESH_MARGIN = 10 * 60         # 만료 10분 전에는 새로 발급
    REQUEST_TIMEOUT = 15

    # 실패 시 호출 중단 시간
    AUTH_FAILURE_COOLDOWN = 60 * 60        # 키·토큰 문제: 1시간
    RATE_LIMIT_COOLDOWN = 60               # 호출 한도 초과(EGW00201 등): 1분
    FAILURE_COOLDOWN = 5 * 60              # 그 밖의 실패: 5분

    DEFAULT_DISCLOSURE_CODES = "I"         # I = 거래소 시장조치 공시 (주식선물 가격제한폭 확대 공시 포함)
    MAX_PAGES_PER_CYCLE = 10               # 코드당 한 주기 최대 되감기 페이지 수 (40건 × 10 = 400건)
    PAGE_INTERVAL = 0.3                    # 페이지 호출 간격 (초당 호출 제한 대비)
    RATE_RETRY_DELAYS = (1.0, 2.0)         # EGW00201(초당 제한) 시 짧게 쉬고 재시도

    def __init__(self, app_key=None, app_secret=None, disclosure_codes=None):
        self.app_key = (app_key or os.getenv('KIS_APP_KEY') or '').strip()
        self.app_secret = (app_secret or os.getenv('KIS_APP_SECRET') or '').strip()
        if disclosure_codes is None:
            disclosure_codes = os.getenv('KIS_DISCLOSURE_CODES', '') or self.DEFAULT_DISCLOSURE_CODES
        if isinstance(disclosure_codes, str):
            disclosure_codes = disclosure_codes.split(',')
        self.disclosure_codes = tuple(c.strip() for c in disclosure_codes if c and c.strip()) \
            or (self.DEFAULT_DISCLOSURE_CODES,)

        # 코드별 워터마크: {코드: (가장 최근에 본 'YYYYMMDDHHMMSS', 그 초에 본 일련번호 집합)}
        self._watermarks = {}

        self._token = None
        self._token_expires_at = 0.0
        self._disabled_until = 0.0
        # 직전 수집 결과 요약 (scraper.last_kind_stats와 같은 모양으로 맞춘다)
        self.last_stats = {'rows': 0, 'rows_recent': 0, 'http_ok': False,
                           'markets': ('KIS',), 'last_status': None}

    # ------------------------------------------------------------------ 상태

    @property
    def is_configured(self):
        """앱키·시크릿이 모두 설정되어 있는지 (템플릿 문구는 미설정으로 취급)"""
        for v in (self.app_key, self.app_secret):
            if not v or v.startswith(('your_', 'YOUR_')) or '앱키' in v:
                return False
        return True

    @property
    def is_available(self):
        return self.is_configured and time.time() >= self._disabled_until

    def _trip(self, seconds, reason):
        self._disabled_until = time.time() + seconds
        print(f"⏸ KIS API 호출을 {seconds}초간 중단합니다 ({reason}).")

    # ------------------------------------------------------------------ 토큰

    def _load_cached_token(self):
        try:
            with open(self.TOKEN_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 다른 앱키로 받은 토큰이면 쓰지 않는다
            if data.get('app_key_tail') != self.app_key[-6:]:
                return False
            self._token = data['access_token']
            self._token_expires_at = float(data['expires_at'])
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _save_cached_token(self):
        """토큰을 원자적으로 저장 (권한 0600). 실패해도 동작은 계속한다."""
        payload = json.dumps({
            'access_token': self._token,
            'expires_at': self._token_expires_at,
            'app_key_tail': self.app_key[-6:],   # 키 전체는 저장하지 않음
        })
        directory = os.path.dirname(os.path.abspath(self.TOKEN_CACHE_FILE)) or '.'
        try:
            fd, tmp = tempfile.mkstemp(prefix='.kis_token.tmp.', dir=directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
                os.replace(tmp, self.TOKEN_CACHE_FILE)
                tmp = None
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as e:
            print(f"⚠️ KIS 토큰 캐시 저장 실패 (동작은 계속): {e}")

    def _invalidate_token(self):
        """토큰이 무효로 판명되면 메모리와 캐시를 모두 지운다 (같은 무효 토큰을 다시 읽지 않도록)"""
        self._token = None
        self._token_expires_at = 0.0
        try:
            os.remove(self.TOKEN_CACHE_FILE)
        except OSError:
            pass

    def _token_valid(self):
        return bool(self._token) and time.time() < self._token_expires_at - self.TOKEN_REFRESH_MARGIN

    async def _ensure_token(self, session):
        """유효한 토큰을 확보. 캐시 → 발급 순. 성공하면 True."""
        if self._token_valid():
            return True
        if self._load_cached_token() and self._token_valid():
            return True

        url = f"{self.BASE_URL}{self.TOKEN_PATH}"
        body = {"grant_type": "client_credentials",
                "appkey": self.app_key, "appsecret": self.app_secret}
        try:
            async with session.post(url, json=body,
                                    headers={"Content-Type": "application/json"},
                                    timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    print(f"❌ KIS 토큰 발급 실패: HTTP {resp.status} - {text[:200]}")
                    # 토큰 발급은 1분에 1회 제한이 있으므로 짧게라도 반드시 쉰다
                    self._trip(self.AUTH_FAILURE_COOLDOWN if resp.status in (401, 403) else self.RATE_LIMIT_COOLDOWN,
                               f"토큰 발급 HTTP {resp.status}")
                    return False
                data = json.loads(text)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            print(f"⚠️ KIS 토큰 발급 중 오류: {detail}")
            self._trip(self.FAILURE_COOLDOWN, "토큰 발급 오류")
            return False

        token = data.get('access_token')
        if not token:
            print(f"❌ KIS 토큰 응답에 access_token이 없습니다: {str(data)[:200]}")
            self._trip(self.AUTH_FAILURE_COOLDOWN, "토큰 응답 이상")
            return False

        self._token = token
        expires_at = None
        raw = data.get('access_token_token_expired')       # 예: "2026-11-27 10:00:00" (KST)
        if raw:
            try:
                expires_at = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp()
            except ValueError:
                expires_at = None
        if expires_at is None:
            expires_at = time.time() + int(data.get('expires_in', 86400))
        self._token_expires_at = expires_at
        self._save_cached_token()
        print("🔑 KIS 접근 토큰을 새로 발급했습니다. (KIS가 카카오 알림톡을 보냅니다. 이후 1일간 재사용)")
        return True

    # ------------------------------------------------------------------ 공시

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self._token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": self.NEWS_TITLE_TR_ID,
            "custtype": "P",
            "tr_cont": "",
        }

    async def fetch_news_titles(self, session, provider_code="", date="", hour=""):
        """공시·시황 제목 목록 원본(output, 최신순 최대 40건)을 반환. 실패 시 None.

        provider_code: 제공업체코드 (빈 값이면 전체)
        date, hour   : 'YYYYMMDD', 'HHMMSS' — 주면 그 시각 이전 목록, 비우면 현재 기준 최신 목록
        """
        if not self.is_available:
            return None
        if not await self._ensure_token(session):
            return None

        params = {
            "FID_NEWS_OFER_ENTP_CODE": provider_code,
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": "",
            "FID_TITL_CNTT": "",              # 서버가 무시함 (실측) → 로컬에서 거른다
            "FID_INPUT_DATE_1": f"00{date}" if date else "",
            "FID_INPUT_HOUR_1": f"0000{hour}" if hour else "",
            "FID_RANK_SORT_CLS_CODE": "",
            "FID_INPUT_SRNO": "",
        }
        url = f"{self.BASE_URL}{self.NEWS_TITLE_PATH}"
        for attempt in range(len(self.RATE_RETRY_DELAYS) + 1):
            last_try = attempt == len(self.RATE_RETRY_DELAYS)
            try:
                async with session.get(url, headers=self._headers(), params=params,
                                       timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)) as resp:
                    self.last_stats['last_status'] = resp.status
                    status = resp.status
                    text = await resp.text()
            except Exception as e:
                detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                print(f"⚠️ KIS 공시 조회 중 오류: {detail}")
                self._trip(self.FAILURE_COOLDOWN, type(e).__name__)
                return None

            rate_limited = status == 429 or 'EGW00201' in text
            if rate_limited and not last_try:
                # 초당 호출 제한은 잠깐 쉬면 풀린다 → 회로를 끊지 않고 재시도
                await asyncio.sleep(self.RATE_RETRY_DELAYS[attempt])
                continue

            if status != 200:
                print(f"⚠️ KIS 공시 조회 실패: HTTP {status} - {text[:200]}")
                if status in (401, 403):
                    self._invalidate_token()   # 토큰 만료·무효 가능성 → 다음엔 재발급
                    self._trip(self.RATE_LIMIT_COOLDOWN, f"HTTP {status}")
                elif rate_limited:
                    self._trip(self.RATE_LIMIT_COOLDOWN, "호출 한도 초과")
                else:
                    self._trip(self.FAILURE_COOLDOWN, f"HTTP {status}")
                return None
            try:
                data = json.loads(text)
            except ValueError:
                print(f"⚠️ KIS 공시 조회 응답 해석 실패: {text[:200]}")
                self._trip(self.FAILURE_COOLDOWN, "응답 해석 실패")
                return None

            if str(data.get('rt_cd')) != '0':
                msg_cd, msg = data.get('msg_cd', ''), data.get('msg1', '')
                print(f"⚠️ KIS 공시 조회 응답 오류: {msg_cd} {msg}")
                if msg_cd in ('EGW00123', 'EGW00121'):       # 토큰 만료·무효
                    self._invalidate_token()
                self._trip(self.RATE_LIMIT_COOLDOWN if msg_cd == 'EGW00201' else self.FAILURE_COOLDOWN,
                           msg_cd or "rt_cd≠0")
                return None

            output = data.get('output') or []
            if isinstance(output, dict):
                output = [output]
            return output
        return None

    @staticmethod
    def _ts(item):
        """정렬·비교용 시각 'YYYYMMDDHHMMSS' (형식이 이상하면 빈 문자열)"""
        ts = f"{str(item.get('data_dt', '')).strip()}{str(item.get('data_tm', '')).strip()}"
        return ts if len(ts) == 14 and ts.isdigit() else ""

    @staticmethod
    def _srno(item):
        return str(item.get('cntt_usiq_srno', '')).strip()

    @staticmethod
    def _extract_corp_name(title):
        """'(주)두산 주식선물 2단계 …' / '올릭스 주식회사 주식선물 …' → '두산' / '올릭스'.
        찾지 못하면 빈 문자열."""
        head = title.split("주식선물", 1)[0] if "주식선물" in title else ""
        for token in ("주식회사", "(주)", "㈜", "[", "]"):
            head = head.replace(token, " ")
        return " ".join(head.split())

    def normalize(self, item):
        """KIS 응답 1건을 봇 내부 공시 형식으로 변환"""
        title = str(item.get('hts_pbnt_titl_cntt', '')).strip()
        tm = str(item.get('data_tm', '')).strip()
        time_str = f"{tm[:2]}:{tm[2:4]}" if len(tm) >= 4 else tm
        return {
            'source': 'KIS',
            'date': str(item.get('data_dt', '')).strip(),
            'time': time_str,
            'corp_name': self._extract_corp_name(title),
            'title': title,
            'id': f"KIS-{item.get('cntt_usiq_srno', '')}" if item.get('cntt_usiq_srno') else "",
            # KIS는 원문 링크를 주지 않는다. 휴대폰(가정·통신사 회선)에서는 KIND가 열리므로 KIND 공시 화면으로 안내
            'link': "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
            # KIS 제목 API는 유가/코스닥 구분을 주지 않는다
            'market': '',
            'stock_code': str(item.get('iscd1', '')).strip(),
        }

    @classmethod
    def _make_watermark(cls, rows):
        """행 목록에서 가장 최근 시각과 그 시각의 일련번호 집합을 뽑는다."""
        stamped = [(cls._ts(x), cls._srno(x)) for x in rows if cls._ts(x)]
        if not stamped:
            return None
        newest = max(ts for ts, _ in stamped)
        return newest, {sr for ts, sr in stamped if ts == newest}

    @staticmethod
    def _merge_watermark(old, new):
        if old is None:
            return new
        if new is None or new[0] < old[0]:
            return old
        if new[0] == old[0]:
            return old[0], old[1] | new[1]
        return new

    async def _collect_code(self, session, code):
        """코드 하나의 새 행을 모은다.

        반환: (새 행 목록, 최신 페이지 행 수, 성공 여부)
        최신 페이지부터 시작해, 페이지의 가장 오래된 행이 아직 워터마크보다 새로우면
        그 시각으로 되감아 다음 페이지를 받는다. 워터마크에 닿거나 페이지 상한에 이르면 멈춘다.
        """
        watermark = self._watermarks.get(code)
        first = await self.fetch_news_titles(session, provider_code=code)
        if first is None:
            return [], 0, False
        first_rows = len(first)

        if watermark is None:
            # 첫 실행: 최신 페이지로 기준점만 잡고 과거는 거슬러 올라가지 않는다 (워밍업에서 발송 생략)
            self._watermarks[code] = self._make_watermark(first)
            return list(first), first_rows, True

        wm_ts, wm_srnos = watermark
        collected = {}
        page, pages = first, 1
        reached = False
        while True:
            new_in_page = 0
            for x in page:
                sr = self._srno(x)
                key = sr or f"{self._ts(x)}|{x.get('hts_pbnt_titl_cntt', '')}"
                if key in collected:
                    continue
                ts = self._ts(x)
                if ts and (ts < wm_ts or (ts == wm_ts and sr in wm_srnos)):
                    continue      # 이미 지난 주기에 본 행
                collected[key] = x
                new_in_page += 1

            stamps = [self._ts(x) for x in page if self._ts(x)]
            if len(page) < 40 or not stamps:
                reached = True    # 더 거슬러 올라갈 행이 없다
                break
            oldest = min(stamps)
            if oldest < wm_ts or (oldest == wm_ts and any(self._srno(x) in wm_srnos for x in page)):
                reached = True
                break
            if pages >= self.MAX_PAGES_PER_CYCLE:
                print(f"⚠️ KIS[{code}] 되감기 상한({self.MAX_PAGES_PER_CYCLE}페이지)에 도달 — "
                      f"{oldest[:8]} {oldest[8:]} 이전 일부 공시를 놓쳤을 수 있습니다.")
                break

            cursor = datetime.strptime(oldest, "%Y%m%d%H%M%S")
            if new_in_page == 0:
                # 같은 초에 40건 넘게 몰려 더 내려가지 못함 → 1초 뒤로 (그 초의 일부는 못 볼 수 있음)
                print(f"⚠️ KIS[{code}] {oldest[:8]} {oldest[8:]}에 공시가 40건 넘게 몰려 1초 건너뜁니다 "
                      f"(그 초의 일부 공시를 놓쳤을 수 있음).")
                cursor -= timedelta(seconds=1)
            await asyncio.sleep(self.PAGE_INTERVAL)
            page = await self.fetch_news_titles(session, provider_code=code,
                                                date=cursor.strftime("%Y%m%d"), hour=cursor.strftime("%H%M%S"))
            pages += 1
            if page is None:
                # 중간 실패: 받은 것은 돌려주되 워터마크는 그대로 둔다 (다음 주기에 다시 채움, 중복은 logic이 거름)
                return list(collected.values()), first_rows, True
            if not page:
                reached = True
                break

        rows = list(collected.values())
        # 상한에 걸려도 워터마크는 앞으로 옮긴다 (계속 과거만 붙잡고 있으면 새 공시를 영영 못 따라감)
        self._watermarks[code] = self._merge_watermark(watermark, self._make_watermark(rows))
        if pages > 1:
            print(f"🔁 KIS[{code}] {pages}페이지 되감아 새 공시 {len(rows)}건 수집"
                  + ("" if reached else " (상한 도달)"))
        return rows, first_rows, True

    async def fetch_disclosures(self, session):
        """오늘자 새 공시를 봇 내부 형식으로 반환. last_stats를 갱신한다.

        반환 형식은 scraper.fetch_kind()와 같아서 logic.filter_disclosures()에 그대로 넣을 수 있다.
        (가격제한폭 공시 판별은 logic.py의 정규식이 한다)
        """
        raw, latest_rows, any_ok = [], 0, False
        for i, code in enumerate(self.disclosure_codes):
            if i:
                await asyncio.sleep(self.PAGE_INTERVAL)
            rows, first_rows, ok = await self._collect_code(session, code)
            raw.extend(rows)
            latest_rows += first_rows
            any_ok = any_ok or ok
            if not self.is_available:
                break     # 회로가 끊겼으면 나머지 코드는 다음 주기에

        today = datetime.now(KST).strftime('%Y%m%d')
        items = [self.normalize(x) for x in raw]
        # 날짜가 비어 있으면 통과, 있으면 오늘 것만 (어제 공시가 새벽에 다시 잡히는 것 방지)
        items = [d for d in items if not d['date'] or d['date'] == today]

        self.last_stats.update({
            'rows': len(raw),
            # 감시(watchdog)용: 최신 페이지에서 받은 행 수 (필터 전). 공시 코드는 항상 최근 40건을 주므로
            # 응답이 정상이면 0이 아니고, 0이면 코드·응답 형식 이상을 의심할 수 있다.
            'rows_recent': latest_rows,
            'http_ok': any_ok,
            'markets': tuple(f"KIS:{c}" for c in self.disclosure_codes),
        })
        return items
