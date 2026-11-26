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

        # 2. 제목 필터 조회 (봇이 실제로 쓰는 방식)
        flt = kis.title_filter
        filtered = await kis.fetch_news_titles(session, title_filter=flt)
        if filtered is None:
            print("❌ 제목 필터 조회 실패")
            return 1
        print(f"\n═══ 제목 필터 '{flt}' 조회: {len(filtered)}건 ═══")
        for item in filtered[:15]:
            d = kis.normalize(item)
            hit = "🎯" if pattern.search(d['title']) else "  "
            print(f"  {hit} {d['date']} {d['time']}  {d['title']}")
            if hit.strip():
                print(f"       → 종목명 추출: '{d['corp_name']}'  종목코드: {d['stock_code'] or '-'}")

        await asyncio.sleep(1)  # 호출 간격

        # 3. 필터 없이 최신 목록 (서버 측 제목 필터가 실제로 동작하는지 비교용)
        latest = await kis.fetch_news_titles(session, title_filter="")
        if latest is None:
            print("\n⚠️ 전체 최신 목록 조회 실패")
        else:
            print(f"\n═══ 필터 없이 최신 목록: {len(latest)}건 (상위 10건) ═══")
            for item in latest[:10]:
                d = kis.normalize(item)
                print(f"     {d['date']} {d['time']}  {d['title'][:60]}")
            matches = [kis.normalize(x)['title'] for x in latest if pattern.search(str(x.get('hts_pbnt_titl_cntt', '')))]
            print(f"   → 이 중 봇이 잡는 공시(가격제한폭 확대): {len(matches)}건")

    print("\n═══ 판단 기준 ═══")
    print("  · 제목 필터 결과에 '가격제한폭'이 들어간 제목만 나오면 → 서버 측 필터 정상 (봇 설정 그대로 사용)")
    print("  · 제목 필터 결과가 필터 없는 목록과 똑같다면 → 서버 측 필터가 동작하지 않음 (알려주세요)")
    print("  · 🎯 표시가 붙은 제목이 오늘 공시라면 → 봇이 그대로 알림을 보낼 수 있음")
    print("  · 장중이 아니거나 해당 공시가 없는 날은 🎯가 0건일 수 있습니다 (정상)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
