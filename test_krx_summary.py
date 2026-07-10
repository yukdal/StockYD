# test_krx_summary.py
# 알림 봇이 실제로 사용하는 "종목명 → 전일 선물 요약" 기능과
# 텔레그램 메시지 최종 모양까지 한 번에 확인하는 테스트 스크립트

import asyncio                                  # 비동기 실행
import sys                                      # 터미널 인코딩 설정
import aiohttp                                  # HTTP 요청
from dotenv import load_dotenv                  # .env 로드
from krx_api import KRXOpenAPI                  # KRX API 모듈
from formatter import DisclosureFormatter       # 텔레그램 메시지 포맷터

load_dotenv()                                   # .env에서 KRX_AUTH_KEY 읽기

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass


async def main():
    krx = KRXOpenAPI()                          # KRX API 객체 생성

    async with aiohttp.ClientSession() as session:      # HTTP 세션 열기
        # 실제 알림 상황을 흉내: '삼성전자' 공시가 떴다고 가정하고 전일 선물 요약 조회
        for corp in ["삼성전자", "BGF리테일"]:          # 두 종목으로 테스트
            summary = await krx.get_futures_summary(session, corp)   # 봇이 쓰는 바로 그 함수 호출
            print(f"=== {corp} 전일 선물 요약 ===")
            print(summary)                              # 요약 딕셔너리 출력
            print()

        # 가짜 공시 데이터로 텔레그램 메시지 최종 모양 확인 (전송은 하지 않음)
        fake_disc = {
            'market': '[유]',                            # 시장 구분
            'corp_name': '삼성전자',                     # 종목명
            'phase': 3,                                  # 3단계 가정
            'direction': '상승',                         # 상승 가정
            'time': '10:30:00',                          # 공시 시각
            'link': 'https://kind.krx.co.kr',            # 링크
        }
        summary = await krx.get_futures_summary(session, '삼성전자')  # 삼성전자 요약 재조회 (캐시라 즉시)
        message = DisclosureFormatter.format_telegram_message(fake_disc, summary)  # 최종 메시지 조립
        print("=== 텔레그램 발송 메시지 미리보기 ===")
        print(message)                                   # 실제 발송될 메시지 모양 출력


if __name__ == "__main__":
    asyncio.run(main())
