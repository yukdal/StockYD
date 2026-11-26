"""KIS 공시 제목 API 2차 탐색 (일회성 진단용)

1차 결과: 제공업체코드(FID_NEWS_OFER_ENTP_CODE) 필터는 동작하고 'G'=코스닥 공시,
FID_INPUT_HOUR_1로 지난 시각부터 거슬러 조회 가능.
이번엔 (1) 모든 제공업체코드를 훑어 공시 계열 코드를 찾고,
(2) 공시 계열 코드로 최근 며칠을 거슬러 올라가며 '주식선물/가격제한폭' 제목이 실제로 오는지 확인합니다.
저장된 토큰을 재사용하므로 카카오 알림톡이 다시 오지 않습니다.

사용법:
    ./venv/bin/python kis_probe2.py
"""
import asyncio
import collections
import string
from datetime import datetime, timedelta

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from kis_api import KISOpenAPI  # noqa: E402

EMPTY = {
    "FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": "",
    "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "",
    "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": "",
}
KEYWORDS = ("가격제한폭", "주식선물")
MAX_PAGES = 80          # 코드당 최대 거슬러 조회 횟수
LOOKBACK_DAYS = 4       # 며칠 전까지 거슬러 볼지


async def query(session, kis, **override):
    params = dict(EMPTY, **override)
    for _ in range(3):
        async with session.get(kis.BASE_URL + kis.NEWS_TITLE_PATH, headers=kis._headers(), params=params,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
        if data.get('msg_cd') == 'EGW00201':   # 초당 호출 제한
            await asyncio.sleep(1.5)
            continue
        return data.get('rt_cd'), data.get('msg1'), data.get('output') or []
    return None, '호출 제한 반복', []


def t(item, n=60):
    return str(item.get('hts_pbnt_titl_cntt', ''))[:n]


def has_kw(item):
    title = str(item.get('hts_pbnt_titl_cntt', ''))
    return any(k in title for k in KEYWORDS)


async def rewind(session, kis, code, stop_dt):
    """code 하나를 최신부터 stop_dt까지 거슬러 조회. (페이지 수, 총 건수, 키워드 제목 목록, 가장 오래된 시각, 막힘 여부)"""
    date, hour = "", ""
    seen, hits, pages = set(), [], 0
    oldest, stuck = None, False
    while pages < MAX_PAGES:
        await asyncio.sleep(0.35)
        rc, msg, out = await query(session, kis, FID_NEWS_OFER_ENTP_CODE=code,
                                   FID_INPUT_DATE_1=date, FID_INPUT_HOUR_1=hour)
        pages += 1
        if rc != '0' or not out:
            break
        new = [x for x in out if x.get('cntt_usiq_srno') not in seen]
        for x in new:
            seen.add(x.get('cntt_usiq_srno'))
            if has_kw(x):
                hits.append(x)
        last = out[-1]
        cur = datetime.strptime(last['data_dt'] + last['data_tm'], "%Y%m%d%H%M%S")
        oldest = cur
        if cur < stop_dt:
            break
        if not new:
            # 같은 초에 40건 넘게 몰려 더 못 내려감 → 1초 빼고 계속 (그 초의 일부는 못 봄)
            stuck = True
            cur -= timedelta(seconds=1)
        date, hour = "00" + cur.strftime("%Y%m%d"), "0000" + cur.strftime("%H%M%S")
    return pages, len(seen), hits, oldest, stuck


async def main():
    kis = KISOpenAPI()
    if not kis.is_configured:
        print("❌ .env에 KIS_APP_KEY / KIS_APP_SECRET이 없습니다.")
        return
    async with aiohttp.ClientSession() as session:
        if not await kis._ensure_token(session):
            print("❌ 토큰을 받지 못했습니다.")
            return

        # 1. 모든 제공업체코드 훑기
        print("[1] 제공업체코드 전수 조사 (결과가 있는 코드만 표시)")
        disclosure_codes = []
        for code in string.digits + string.ascii_uppercase + string.ascii_lowercase:
            await asyncio.sleep(0.35)
            rc, msg, out = await query(session, kis, FID_NEWS_OFER_ENTP_CODE=code)
            if not out:
                continue
            own = [x for x in out if x.get('news_ofer_entp_code') == code]
            if not own:
                print(f"    코드 {code}: 필터 무시됨(다른 코드가 섞여 옴) → 건너뜀")
                continue
            dorgs = dict(collections.Counter(x.get('dorg') for x in own))
            kw = sum(1 for x in own if has_kw(x))
            rng = f"{own[-1].get('data_dt')} {own[-1].get('data_tm')} ~ {own[0].get('data_dt')} {own[0].get('data_tm')}"
            print(f"    코드 {code}: {len(own)}건 {dorgs}  범위 {rng}  키워드 {kw}건")
            if any('공시' in str(d) or '거래소' in str(d) or 'KRX' in str(d) for d in dorgs):
                disclosure_codes.append(code)
                print(f"        예) {t(own[0])}")

        # 2. 공시 계열 코드로 최근 며칠 거슬러 올라가며 키워드 제목 찾기
        stop_dt = datetime.now() - timedelta(days=LOOKBACK_DAYS)
        print(f"\n[2] 공시 계열 코드 {disclosure_codes} 를 {stop_dt:%m-%d %H:%M}까지 거슬러 조회")
        for code in disclosure_codes:
            pages, total, hits, oldest, stuck = await rewind(session, kis, code, stop_dt)
            print(f"    코드 {code}: {pages}페이지, {total}건, 가장 오래된 {oldest}, "
                  f"같은초 몰림 {'있음' if stuck else '없음'}, 키워드 제목 {len(hits)}건")
            for x in hits[:15]:
                print(f"        {x.get('data_dt')} {x.get('data_tm')} [{x.get('news_lrdv_code')}] {t(x, 80)}")


if __name__ == "__main__":
    asyncio.run(main())
