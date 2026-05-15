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

# .env 파일 로드 (로컬 환경용)
load_dotenv()

# 윈도우 터미널 인코딩 문제 해결 (UTF-8 강제 설정)
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Python 3.7 미만 버전 대응 (필요시)
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def run_monitor():
    print("🚀 실시간 주식선물 공시 모니터링 시작...")
    
    scraper = DisclosureScraper()
    logic = DisclosureLogic()
    notifier = TelegramNotifier()
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # 1. 데이터 수집 (병렬 처리)
                kind_task = scraper.fetch_kind(session)
                dart_task = scraper.fetch_dart(session)
                
                results = await asyncio.gather(kind_task, dart_task)
                all_disclosures = results[0] + results[1]
                
                # 2. 필터링 및 우선순위 정렬
                filtered = logic.filter_disclosures(all_disclosures)
                
                # 3. 알림 전송
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
