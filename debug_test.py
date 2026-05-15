import asyncio
import aiohttp
from scraper import DisclosureScraper
from logic import DisclosureLogic
from notifier import TelegramNotifier
from dotenv import load_dotenv
import os

load_dotenv()

import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def debug_test():
    print("--- DEBUG START ---")
    scraper = DisclosureScraper()
    logic = DisclosureLogic()
    notifier = TelegramNotifier()
    
    async with aiohttp.ClientSession() as session:
        print("1. Fetching KIND...")
        kind_disclosures = await scraper.fetch_kind(session)
        print(f"   Found {len(kind_disclosures)} KIND disclosures.")
        
        # Print first 5 KIND disclosures for format check
        for d in kind_disclosures[:5]:
            print(f"   - {d['corp_name']}: {d['title']}")

        print("\n2. Fetching DART...")
        dart_disclosures = await scraper.fetch_dart(session)
        print(f"   Found {len(dart_disclosures)} DART disclosures.")
        
        # Print first 5 DART disclosures
        for d in dart_disclosures[:5]:
            print(f"   - {d['corp_name']}: {d['title']}")

        all_disclosures = kind_disclosures + dart_disclosures
        
        print("\n3. Testing Filtering Logic...")
        # Let's see if we can find ANY disclosure with "가격제한폭" to see if the pattern matches
        potential_matches = [d for d in all_disclosures if "가격제한폭" in d['title']]
        print(f"   Found {len(potential_matches)} titles containing '가격제한폭'")
        for d in potential_matches:
            print(f"   - Match candidate: {d['title']}")
            
        filtered = logic.filter_disclosures(all_disclosures)
        print(f"   Filtered results (logic.filter_disclosures): {len(filtered)}")

        if len(filtered) == 0:
            print("\n⚠️ No matches found for the specific criteria (Stock Futures 2/3 Phase).")
            print("Trying a test notification to verify Telegram settings...")
            success = await notifier.send_message("🛠 [시스템 테스트] 알림 기능 정상 작동 확인 중입니다.", session)
            if success:
                print("✅ Telegram Test Message Sent Successfully!")
            else:
                print("❌ Telegram Test Message Failed!")
        else:
            print(f"\n✅ Found {len(filtered)} matches! Sending first one as test...")
            from formatter import DisclosureFormatter
            message = DisclosureFormatter.format_telegram_message(filtered[0])
            success = await notifier.send_message(message, session)
            if success:
                print("✅ Notification Sent!")
            else:
                print("❌ Notification Failed!")

if __name__ == "__main__":
    asyncio.run(debug_test())
