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
#   KIS_TITLE_FILTER=가격제한폭   (선택. 서버 측 제목 검색어. 빈 값이면 전체 조회 후 로컬 필터)

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

    DEFAULT_TITLE_FILTER = "가격제한폭"

    def __init__(self, app_key=None, app_secret=None, title_filter=None):
        self.app_key = (app_key or os.getenv('KIS_APP_KEY') or '').strip()
        self.app_secret = (app_secret or os.getenv('KIS_APP_SECRET') or '').strip()
        if title_filter is None:
            title_filter = os.getenv('KIS_TITLE_FILTER', self.DEFAULT_TITLE_FILTER)
        self.title_filter = title_filter.strip()

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

    async def fetch_news_titles(self, session, title_filter=None):
        """공시·시황 제목 목록 원본(output)을 반환. 실패 시 None.

        모든 조회 조건을 비우면 '현재 기준 최신' 목록이 온다.
        title_filter가 있으면 서버 측 제목 검색(FID_TITL_CNTT)에 넣는다.
        """
        if not self.is_available:
            return None
        if not await self._ensure_token(session):
            return None

        params = {
            "FID_NEWS_OFER_ENTP_CODE": "",
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": "",
            "FID_TITL_CNTT": self.title_filter if title_filter is None else title_filter,
            "FID_INPUT_DATE_1": "",
            "FID_INPUT_HOUR_1": "",
            "FID_RANK_SORT_CLS_CODE": "",
            "FID_INPUT_SRNO": "",
        }
        url = f"{self.BASE_URL}{self.NEWS_TITLE_PATH}"
        try:
            async with session.get(url, headers=self._headers(), params=params,
                                   timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)) as resp:
                self.last_stats['last_status'] = resp.status
                text = await resp.text()
                if resp.status != 200:
                    print(f"⚠️ KIS 공시 조회 실패: HTTP {resp.status} - {text[:200]}")
                    if resp.status in (401, 403):
                        self._invalidate_token()   # 토큰 만료·무효 가능성 → 다음엔 재발급
                        self._trip(self.RATE_LIMIT_COOLDOWN, f"HTTP {resp.status}")
                    elif resp.status == 429 or 'EGW00201' in text:
                        self._trip(self.RATE_LIMIT_COOLDOWN, "호출 한도 초과")
                    else:
                        self._trip(self.FAILURE_COOLDOWN, f"HTTP {resp.status}")
                    return None
                data = json.loads(text)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            print(f"⚠️ KIS 공시 조회 중 오류: {detail}")
            self._trip(self.FAILURE_COOLDOWN, type(e).__name__)
            return None

        if str(data.get('rt_cd')) != '0':
            msg_cd, msg = data.get('msg_cd', ''), data.get('msg1', '')
            print(f"⚠️ KIS 공시 조회 응답 오류: {msg_cd} {msg}")
            if msg_cd in ('EGW00123', 'EGW00121'):       # 토큰 만료·무효
                self._invalidate_token()
            self._trip(self.RATE_LIMIT_COOLDOWN if msg_cd == 'EGW00201' else self.FAILURE_COOLDOWN, msg_cd or "rt_cd≠0")
            return None

        output = data.get('output') or []
        if isinstance(output, dict):
            output = [output]
        return output

    @staticmethod
    def _extract_corp_name(title):
        """'(주)두산 주식선물 2단계 …' → '두산'. 찾지 못하면 빈 문자열."""
        head = title.split("주식선물", 1)[0] if "주식선물" in title else ""
        for token in ("(주)", "㈜", "[", "]"):
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

    async def fetch_disclosures(self, session):
        """오늘자 공시를 봇 내부 형식으로 반환. last_stats를 갱신한다.

        반환 형식은 scraper.fetch_kind()와 같아서 logic.filter_disclosures()에 그대로 넣을 수 있다.
        """
        raw = await self.fetch_news_titles(session)
        if raw is None:
            self.last_stats.update({'rows': 0, 'http_ok': False})
            return []

        today = datetime.now(KST).strftime('%Y%m%d')
        items = [self.normalize(x) for x in raw]
        # 날짜가 비어 있으면 통과, 있으면 오늘 것만 (어제 공시가 새벽에 다시 잡히는 것 방지)
        items = [d for d in items if not d['date'] or d['date'] == today]

        self.last_stats.update({
            'rows': len(raw),
            # 제목 필터를 쓰면 0건이 정상(그 시간에 해당 공시가 없음)이므로
            # 감시(watchdog)의 '0건 지속' 판정에 걸리지 않도록 응답 성공 여부만 반영한다
            'rows_recent': len(raw) if not self.title_filter else 1,
            'http_ok': True,
        })
        return items
