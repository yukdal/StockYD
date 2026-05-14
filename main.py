import asyncio
import aiohttp
import os
from scraper import DisclosureScraper
from logic import DisclosureLogic
from formatter import DisclosureFormatter
from notifier import TelegramNotifier
from dotenv import load_dotenv

# .env 파일 로드 (로컬 환경용)
load_dotenv()

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
                        print(f"✅ 알림 전송 성공: {disc['corp_name']} ({disc['phase']}단계)")
                    
                    # 봇 차단 방지를 위한 미세 지연
                    await asyncio.sleep(0.5)
                
                # 4. 폴링 주기 지연 (3~5초)
                await asyncio.sleep(3)
                
            except Exception as e:
                print(f"❌ 루프 오류 발생: {e}")
                await asyncio.sleep(10) # 오류 시 잠시 대기 후 재시도

if __name__ == "__main__":
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\n⏹ 모니터링 종료")
