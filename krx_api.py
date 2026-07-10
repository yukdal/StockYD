# krx_api.py
# KRX 공식 Open API (openapi.krx.co.kr) 연동 모듈
# - 회원가입 후 발급받은 인증키(AUTH_KEY)를 .env의 KRX_AUTH_KEY에 넣으면 동작합니다.
# - 주의: KRX Open API는 "일별(전일)" 데이터입니다. 실시간이 아니므로
#   실시간 공시 감지(KIND/DART)는 그대로 두고, 알림에 "전일 매매정보"를 보강하는 용도입니다.

import os                                    # 환경변수(.env에 저장된 인증키)를 읽기 위한 표준 모듈
import asyncio                               # 비동기 처리(기존 봇이 asyncio 기반이므로 동일하게 사용)
from datetime import datetime, timedelta, timezone  # 날짜 계산(전일/영업일 계산)용


class KRXOpenAPI:
    """KRX 공식 Open API 호출 클래스 (주식선물 일별매매정보)"""

    # KRX Open API의 실제 데이터 서버 주소 (문서 기준)
    BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"

    # 서비스별 엔드포인트 경로 (✅ 2026-07-10 실제 호출로 검증 완료: 3,080개 종목 수신 확인)
    ENDPOINTS = {
        "주식선물(유가)": "/drv/eqsfu_stk_bydd_trd",    # 주식선물(유가증권 기초자산) 일별매매정보
        "주식선물(코스닥)": "/drv/eqkfu_ksq_bydd_trd",  # 주식선물(코스닥 기초자산) 일별매매정보
    }

    def __init__(self, auth_key=None):
        # 인증키: 인자로 받거나, 없으면 .env의 KRX_AUTH_KEY에서 읽음
        self.auth_key = auth_key or os.getenv('KRX_AUTH_KEY')
        # 발급 전 placeholder 문구가 그대로 있으면 "미설정"으로 취급
        if self.auth_key and "인증키" in self.auth_key:
            self.auth_key = None
        # 같은 날짜 데이터를 반복 조회하지 않도록 메모리에 캐시 {날짜문자열: 데이터리스트}
        self._cache = {}

    @property
    def is_configured(self):
        """인증키가 설정되어 있는지 여부 (True/False)"""
        return bool(self.auth_key)

    async def _fetch_endpoint(self, session, endpoint, bas_dd):
        """단일 엔드포인트를 1회 호출하여 데이터 리스트를 반환 (실패 시 빈 리스트)"""
        url = f"{self.BASE_URL}{endpoint}"                 # 전체 요청 URL 조립
        headers = {"AUTH_KEY": self.auth_key}              # KRX는 헤더에 AUTH_KEY를 담아 인증
        params = {"basDd": bas_dd}                         # basDd = 조회 기준일 (YYYYMMDD)

        try:
            # 1차 시도: GET 방식 (KRX 공식 문서 예시 방식)
            async with session.get(url, headers=headers, params=params, timeout=10) as resp:
                if resp.status == 200:                     # 정상 응답이면
                    data = await resp.json(content_type=None)  # JSON 파싱 (Content-Type이 달라도 강제 파싱)
                    # 응답의 최상위 키는 "OutBlock_1" (✅ 실제 응답으로 검증 완료)
                    return data.get("OutBlock_1", [])
                # 인증 실패(401/403)면 키 문제이므로 명확히 로그 출력
                if resp.status in (401, 403):
                    print(f"❌ KRX API 인증 실패({resp.status}): 인증키 또는 해당 서비스 이용신청 여부를 확인하세요.")
                    return []
        except Exception as e:
            print(f"⚠️ KRX API 호출 오류({endpoint}): {e}")
        return []                                          # 모든 실패 케이스는 빈 리스트 반환 (봇 전체가 죽지 않도록)

    async def get_day_data(self, session, bas_dd):
        """특정 날짜의 주식선물(유가+코스닥) 전체 데이터를 합쳐서 반환 (캐시 사용)"""
        if bas_dd in self._cache:                          # 이미 조회한 날짜면
            return self._cache[bas_dd]                     # 캐시에서 즉시 반환 (API 호출 절약)

        all_rows = []                                      # 두 시장 데이터를 담을 리스트
        for name, endpoint in self.ENDPOINTS.items():      # 유가/코스닥 엔드포인트 각각에 대해
            rows = await self._fetch_endpoint(session, endpoint, bas_dd)  # API 호출
            all_rows.extend(rows)                          # 결과 합치기
            await asyncio.sleep(0.2)                       # 연속 호출 사이 짧은 지연 (서버 부하 방지)

        if all_rows:                                       # 데이터가 있으면
            self._cache[bas_dd] = all_rows                 # 캐시에 저장 (하루에 한 번만 실제 호출)
            # 캐시가 무한히 커지지 않도록 오래된 날짜는 정리 (최근 3개 날짜만 유지)
            for old_key in list(self._cache.keys())[:-3]:
                del self._cache[old_key]
        return all_rows

    def _recent_business_days(self, max_back=7):
        """오늘(KST) 기준 직전 영업일 후보들을 최신순으로 반환 (주말 제외, 공휴일은 API 빈응답으로 자연 스킵)"""
        kst = timezone(timedelta(hours=9))                 # 한국 시간대 정의
        day = datetime.now(kst) - timedelta(days=1)        # KRX 일별 데이터는 전일분부터 존재하므로 어제부터 시작
        days = []                                          # 후보 날짜를 담을 리스트
        while len(days) < max_back:                        # 최대 max_back개까지
            if day.weekday() < 5:                          # 월(0)~금(4)만 (토=5, 일=6 제외)
                days.append(day.strftime('%Y%m%d'))        # YYYYMMDD 형식으로 추가
            day -= timedelta(days=1)                       # 하루 뒤로 이동
        return days

    async def get_futures_summary(self, session, corp_name):
        """종목명(예: 삼성전자)으로 가장 최근 영업일의 주식선물 매매정보 요약을 반환 (없으면 None)"""
        if not self.is_configured:                         # 인증키가 없으면
            return None                                    # 조용히 건너뜀 (알림 기능에 영향 없음)

        for bas_dd in self._recent_business_days():        # 최근 영업일부터 차례로
            rows = await self.get_day_data(session, bas_dd)  # 그 날짜의 전체 데이터 조회
            if not rows:                                   # 데이터가 없으면 (공휴일 등)
                continue                                   # 하루 더 이전 날짜로 재시도

            # 종목명이 포함된 선물 종목만 골라냄
            # 응답 필드명: ISU_NM=종목명, TDD_CLSPRC=종가, ACC_TRDVOL=거래량, ACC_OPNINT_QTY=미결제약정 (✅ 실제 응답으로 검증 완료)
            matches = [r for r in rows if corp_name in str(r.get('ISU_NM', ''))]
            if not matches:                                # 해당 종목 선물이 없으면
                return None                                # 데이터 자체는 있었으므로 더 과거로 갈 필요 없음

            def _to_num(value):
                """'1,234' 같은 문자열 숫자를 float로 변환 (실패 시 0)"""
                try:
                    return float(str(value).replace(',', ''))
                except (ValueError, TypeError):
                    return 0.0

            # 여러 월물(만기) 중 거래량이 가장 많은 종목(=대표 근월물)을 선택
            best = max(matches, key=lambda r: _to_num(r.get('ACC_TRDVOL', 0)))

            return {                                       # 알림 메시지에 쓸 요약 정보 반환
                'base_date': bas_dd,                                   # 기준일
                'name': best.get('ISU_NM', ''),                        # 선물 종목명
                'close': best.get('TDD_CLSPRC', '-'),                  # 종가
                'change': best.get('CMPPREVDD_PRC', '-'),              # 전일 대비
                'volume': best.get('ACC_TRDVOL', '-'),                 # 누적 거래량
                'open_interest': best.get('ACC_OPNINT_QTY', '-'),      # 미결제약정
            }
        return None                                        # 최근 영업일들 모두 데이터가 없으면 None
