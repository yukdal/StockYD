# test_krx_api.py
# KRX 인증키가 제대로 발급/설정되었는지 확인하는 테스트 스크립트
# 사용법: 인증키를 .env의 KRX_AUTH_KEY에 넣은 뒤 → python test_krx_api.py

import asyncio                       # 비동기 실행용 표준 모듈
import sys                           # 터미널 인코딩 설정용
import aiohttp                       # 비동기 HTTP 요청 라이브러리 (기존 봇과 동일)
from dotenv import load_dotenv       # .env 파일을 읽어 환경변수로 등록해주는 라이브러리
from krx_api import KRXOpenAPI       # 방금 만든 KRX API 모듈

load_dotenv()                        # .env 파일 로드 (KRX_AUTH_KEY를 읽기 위해)

# 윈도우 터미널에서 한글/이모지가 깨지지 않도록 UTF-8 강제 설정
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass


async def main():
    krx = KRXOpenAPI()               # KRX API 객체 생성 (.env에서 인증키 자동 로드)

    if not krx.is_configured:        # 인증키가 없거나 placeholder 그대로면
        print("❌ KRX_AUTH_KEY가 설정되지 않았습니다.")
        print("   1) openapi.krx.co.kr 회원가입 → 인증키 발급")
        print("   2) '주식선물(유가/코스닥) 일별매매정보' 서비스 이용 신청")
        print("   3) .env 파일의 KRX_AUTH_KEY= 뒤에 발급받은 키 입력")
        return                       # 키가 없으면 여기서 종료

    async with aiohttp.ClientSession() as session:          # HTTP 세션 열기
        # 최근 영업일 후보 날짜들을 하나씩 시도
        for bas_dd in krx._recent_business_days():
            print(f"🔍 {bas_dd} 데이터 조회 시도 중...")
            rows = await krx.get_day_data(session, bas_dd)   # 해당 날짜 데이터 요청
            if rows:                                         # 데이터를 받았으면 성공
                print(f"✅ 연동 성공! {bas_dd} 기준 주식선물 {len(rows)}개 종목 수신")
                print("--- 샘플 데이터 (첫 2건) ---")
                for row in rows[:2]:                         # 첫 2건만 출력해서
                    print(row)                               # 실제 필드명(ISU_NM 등)을 눈으로 확인
                print("---------------------------")
                print("💡 위 출력의 필드명이 krx_api.py의 필드명(ISU_NM, TDD_CLSPRC 등)과 다르면 수정이 필요합니다.")
                return                                       # 성공했으니 종료
        # 모든 날짜에서 데이터를 못 받은 경우
        print("❌ 데이터 수신 실패. 원인 후보:")
        print("   - 인증키 오타")
        print("   - 해당 API '이용 신청' 미승인 (서비스별로 개별 신청 필요)")
        print("   - 엔드포인트 경로 불일치 (krx_api.py의 ENDPOINTS를 API 상세페이지와 대조)")


if __name__ == "__main__":           # 이 파일을 직접 실행했을 때만
    asyncio.run(main())              # 비동기 main 함수 실행
