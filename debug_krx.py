# debug_krx.py
# KRX API 401 오류의 원인을 좁히기 위한 임시 디버그 스크립트
# (원인 파악 후 삭제해도 되는 파일입니다)

import asyncio                     # 비동기 실행
import os                          # 환경변수 읽기
import sys                         # 터미널 인코딩 설정
import aiohttp                     # HTTP 요청
from dotenv import load_dotenv     # .env 로드

load_dotenv()                      # .env에서 KRX_AUTH_KEY 읽기
KEY = os.getenv("KRX_AUTH_KEY")    # 인증키

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

print("key length:", len(KEY) if KEY else 0)   # 키가 제대로 로드됐는지 길이만 확인 (키 자체는 출력 안 함)

BAS_DD = "20260709"                # 조회 기준일 (최근 영업일)

# 시도해볼 조합: (설명, HTTP방식, URL)
TESTS = [
    ("GET  sto/stk_bydd_trd (주식-유가, 기본 예제 API)", "get",  "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
    ("GET  drv/fut_bydd_trd (선물 일반)",               "get",  "https://data-dbg.krx.co.kr/svc/apis/drv/fut_bydd_trd"),
    ("GET  drv/eqsfu_stk_bydd_trd (주식선물-유가)",      "get",  "https://data-dbg.krx.co.kr/svc/apis/drv/eqsfu_stk_bydd_trd"),
    ("POST drv/eqsfu_stk_bydd_trd (POST 방식 시도)",     "post", "https://data-dbg.krx.co.kr/svc/apis/drv/eqsfu_stk_bydd_trd"),
]


async def probe():
    async with aiohttp.ClientSession() as s:
        for name, method, url in TESTS:
            try:
                if method == "get":
                    # GET: 쿼리스트링으로 basDd 전달, 헤더에 AUTH_KEY
                    async with s.get(url, headers={"AUTH_KEY": KEY}, params={"basDd": BAS_DD}, timeout=10) as r:
                        body = (await r.text())[:300]          # 응답 본문 앞 300자만
                        print(f"[{name}]\n  status={r.status}\n  body={body}\n")
                else:
                    # POST: JSON 본문으로 basDd 전달
                    async with s.post(url, headers={"AUTH_KEY": KEY}, json={"basDd": BAS_DD}, timeout=10) as r:
                        body = (await r.text())[:300]
                        print(f"[{name}]\n  status={r.status}\n  body={body}\n")
            except Exception as e:
                print(f"[{name}] error: {e}\n")


asyncio.run(probe())
