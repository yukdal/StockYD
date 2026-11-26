"""KIS Open API 연동 점검 스크립트

봇을 KIS로 전환하기 전에, 서버에서 실제로 공시 제목이 받아지는지 확인합니다.
키는 .env의 KIS_APP_KEY / KIS_APP_SECRET에서 읽으며, 화면에 키를 출력하지 않습니다.

사용법:
    ./venv/bin/python test_kis.py

참고: 토큰을 처음 발급할 때 KIS가 카카오 알림톡을 보냅니다. 토큰은 .kis_token.json에
저장되어 1일간 재사용되므로, 이 스크립트와 봇이 같은 토큰을 씁니다.
"""
import asyncio
import re
import sys

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from kis_api import KISOpenAPI          # noqa: E402
from logic import DisclosureLogic       # noqa: E402


async def main():
    kis = KISOpenAPI()
    if not kis.is_configured:
        print("❌ .env에 KIS_APP_KEY / KIS_APP_SECRET이 없습니다 (또는 예시 문구 그대로입니다).")
        return 1
    print(f"🔑 앱키: {kis.app_key[:4]}…{kis.app_key[-2:]}  (시크릿은 출력하지 않음)")

    pattern = DisclosureLogic().pattern

    async with aiohttp.ClientSession() as session:
        # 1. 토큰
        if not await kis._ensure_token(session):
            print("❌ 토큰을 받지 못했습니다. 위 오류를 확인하세요.")
            return 1
        print("✅ 토큰 확보")

        # 2. 공시 제공업체코드별 최신 목록 (봇이 실제로 쓰는 방식)
        total_hits = 0
        for code in kis.disclosure_codes:
            rows = await kis.fetch_news_titles(session, provider_code=code)
            if rows is None:
                print(f"❌ 코드 {code} 조회 실패")
                return 1
            own = sum(1 for x in rows if x.get('news_ofer_entp_code') == code)
            print(f"\n═══ 제공업체코드 '{code}' 최신 목록: {len(rows)}건 (그 코드 기사 {own}건) ═══")
            for item in rows[:15]:
                d = kis.normalize(item)
                hit = "🎯" if pattern.search(d['title']) else "  "
                total_hits += bool(hit.strip())
                print(f"  {hit} {d['date']} {d['time']}  [{item.get('dorg', '')}] {d['title'][:60]}")
                if hit.strip():
                    print(f"       → 종목명 추출: '{d['corp_name']}'  종목코드: {d['stock_code'] or '-'}")
            await asyncio.sleep(1)  # 호출 간격

        # 3. 봇과 같은 수집 함수 2회 (1회차 기준점, 2회차는 그 사이 새 공시만)
        first = await kis.fetch_disclosures(session)
        await asyncio.sleep(1)
        second = await kis.fetch_disclosures(session)
        print(f"\n═══ 수집 함수: 1회차 {len(first)}건(기준점), 2회차 새 공시 {len(second)}건, "
              f"최신 페이지 {kis.last_stats['rows_recent']}행 ═══")

    print("\n═══ 판단 기준 ═══")
    print("  · 각 코드 목록이 '그 코드 기사'로만 채워져 있으면 → 제공업체코드 필터 정상")
    print("  · 🎯 표시가 붙은 제목이 오늘 공시라면 → 봇이 그대로 알림을 보낼 수 있음")
    print("  · 장중이 아니거나 해당 공시가 없는 날은 🎯가 0건일 수 있습니다 (정상)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
