"""KIS 공시 제목 API 조회 조건 탐색 스크립트 (일회성 진단용)

서버 측 제목 필터(FID_TITL_CNTT)가 무시되고 최신 40건만 오는 것이 확인되어,
공시만 걸러 받을 수 있는 조건과 지난 시각을 거슬러 조회할 수 있는지 확인합니다.
저장된 토큰(.kis_token.json)을 재사용하므로 카카오 알림톡이 다시 오지 않습니다.

사용법:
    ./venv/bin/python kis_probe.py
"""
import asyncio
import collections

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from kis_api import KISOpenAPI  # noqa: E402

EMPTY = {
    "FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": "",
    "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "",
    "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": "",
}


async def query(session, kis, **override):
    params = dict(EMPTY, **override)
    async with session.get(kis.BASE_URL + kis.NEWS_TITLE_PATH, headers=kis._headers(), params=params,
                           timeout=aiohttp.ClientTimeout(total=15)) as resp:
        data = await resp.json(content_type=None)
        return resp.headers.get('tr_cont'), data.get('rt_cd'), data.get('msg1'), data.get('output') or []


def title(item, n=40):
    return str(item.get('hts_pbnt_titl_cntt', ''))[:n]


async def main():
    kis = KISOpenAPI()
    if not kis.is_configured:
        print("❌ .env에 KIS_APP_KEY / KIS_APP_SECRET이 없습니다.")
        return
    async with aiohttp.ClientSession() as session:
        if not await kis._ensure_token(session):
            print("❌ 토큰을 받지 못했습니다.")
            return

        # 1. 기본 조회 — 응답에 들어 있는 코드 분포
        tr_cont, rt_cd, msg, out = await query(session, kis)
        print(f"[1] 기본 조회: {len(out)}건, tr_cont={tr_cont}, rt_cd={rt_cd} {msg}")
        dist = collections.Counter((x.get('news_ofer_entp_code'), x.get('dorg'), x.get('news_lrdv_code')) for x in out)
        print("    (제공업체코드, 자료원, 대구분) 분포:")
        for key, n in dist.most_common():
            print(f"      {key}: {n}건")
        print("    샘플 20건:")
        for x in out[:20]:
            print(f"      [{x.get('news_ofer_entp_code')}|{x.get('dorg')}|{x.get('news_lrdv_code')}] "
                  f"{x.get('data_tm')} {title(x)}")

        # 2. 제공업체코드별 필터가 실제로 걸러지는지
        print("\n[2] 제공업체코드 필터 시험")
        codes = sorted({x.get('news_ofer_entp_code') for x in out if x.get('news_ofer_entp_code')})
        for code in codes:
            await asyncio.sleep(0.5)
            _, rc, m, o2 = await query(session, kis, FID_NEWS_OFER_ENTP_CODE=code)
            d2 = dict(collections.Counter(x.get('news_ofer_entp_code') for x in o2))
            first = title(o2[0]) if o2 else ''
            print(f"    코드 {code}: {len(o2)}건, 결과 코드분포 {d2}, rt_cd={rc}, 첫 제목: {first}")

        # 3. 지난 시각 기준 조회 (누락 방지용 되감기 가능 여부)
        print("\n[3] 지난 시각 기준 조회")
        for hour in ("0000100000", "0000140000"):
            await asyncio.sleep(0.5)
            tc, rc, m, o3 = await query(session, kis, FID_INPUT_HOUR_1=hour)
            rng = f"{o3[-1].get('data_dt')} {o3[-1].get('data_tm')} ~ {o3[0].get('data_dt')} {o3[0].get('data_tm')}" if o3 else "-"
            print(f"    {hour[4:6]}시 기준: {len(o3)}건, 범위 {rng}, tr_cont={tc}, rt_cd={rc}")

        # 4. 오늘 가격제한폭 공시가 있었는지 (14시 기준 목록에서)
        hits = [x for x in o3 if '가격제한폭' in str(x.get('hts_pbnt_titl_cntt', ''))]
        print(f"\n[4] 14시 기준 목록 중 '가격제한폭' 제목: {len(hits)}건")
        for x in hits[:5]:
            print(f"      [{x.get('news_ofer_entp_code')}|{x.get('dorg')}|{x.get('news_lrdv_code')}] "
                  f"{x.get('data_tm')} {title(x, 60)}")


if __name__ == "__main__":
    asyncio.run(main())
