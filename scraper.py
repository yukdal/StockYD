import aiohttp
import asyncio
from datetime import datetime
import json
import os
from bs4 import BeautifulSoup

class DisclosureScraper:
    KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
    DART_URL = "https://opendart.fss.or.kr/api/list.json"

    # 요청 타임아웃(초).
    # 지정하지 않으면 aiohttp 기본값(총 5분)이 적용되어, 응답이 늦어질 때
    # 감시 루프 전체가 그만큼 멈춰버린다.
    REQUEST_TIMEOUT = 15

    # KIND 시장 구분 (1: 유가증권, 2: 코스닥, 3: 파생상품)
    #
    # ⚠️ 예전에는 매 주기마다 세 시장을 모두 조회했다. 3초 주기와 겹쳐
    # 분당 약 40회를 장중 내내 보냈고, 결국 KRX로부터 IP 차단(HTTP 403)을 당했다.
    # 지금은 한 주기에 한 시장씩 순환 조회하여 요청량을 1/3로 줄인다.
    MARKET_TYPES = ('1', '2', '3')

    def __init__(self, dart_api_key=None):
        self.dart_api_key = dart_api_key or os.getenv('DART_API_KEY')
        # 순환 조회용 커서 (매 호출마다 다음 시장으로 이동)
        self._market_cursor = 0
        # 시장별 최근 파싱 건수. 한 바퀴 순환한 결과를 합쳐 감시(watchdog)에 넘긴다.
        # 한 시장만 보고 판단하면 그 시장에 공시가 없는 시간대에 오탐이 난다.
        self._rows_by_market = {}
        # 직전 KIND 수집 결과 요약
        self.last_kind_stats = {'rows': 0, 'rows_recent': 0, 'http_ok': False,
                                'markets': (), 'last_status': None}
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://kind.krx.co.kr/disclosure/todaydisclosure.do'
        }

    def _next_market(self):
        """다음에 조회할 시장 구분을 하나 반환 (순환)"""
        market = self.MARKET_TYPES[self._market_cursor % len(self.MARKET_TYPES)]
        self._market_cursor += 1
        return market

    async def fetch_kind(self, session, market_types=None):
        """KIND 오늘의 공시 스크래핑

        market_types를 지정하지 않으면 순환 순서에 따라 한 시장만 조회한다.
        (요청량을 줄여 KRX의 차단을 피하기 위함. 자세한 배경은 MARKET_TYPES 주석 참고)

        수집 결과 요약을 self.last_kind_stats에 남긴다.
        '응답은 받았는데 0건'(구조 변경 의심)과 '요청 자체가 실패'(네트워크/차단)를
        구분해야 감시(watchdog) 쪽에서 적절한 경고를 낼 수 있다.
        """
        targets = tuple(market_types) if market_types else (self._next_market(),)

        all_kind_disclosures = []
        http_ok = False       # 이번 호출에서 정상 응답을 받았는지
        last_status = None    # 마지막으로 받은 HTTP 상태 코드 (403 차단 진단용)
        for m_type in targets:
            payload = {
                'method': 'searchTodayDisclosureSub',
                'currentPageSize': '100',
                'pageIndex': '1',
                'orderMode': '1',
                'orderStat': 'D',
                'forward': 'todaydisclosure_sub',
                'marketType': m_type,
            }
            
            try:
                async with session.post(self.KIND_URL, data=payload, headers=self.headers,
                                        timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)) as response:
                    last_status = response.status
                    if response.status == 200:
                        http_ok = True
                        html = await response.text()
                        disclosures = self._parse_kind(html, m_type)
                        all_kind_disclosures.extend(disclosures)
                        self._rows_by_market[m_type] = len(disclosures)
                    elif response.status == 403:
                        # 요청량 초과 등으로 KRX가 접근을 막은 상태.
                        # 계속 두드리면 차단이 길어지므로 호출부에서 백오프해야 한다.
                        print(f"KIND 접근 차단 (Market {m_type}): HTTP 403 — "
                              f"KRX가 요청을 거부하고 있습니다. 요청 주기를 늘리고 잠시 중단하세요.")
                    else:
                        print(f"KIND Error (Market {m_type}): {response.status}")
            except asyncio.TimeoutError:
                print(f"KIND 응답 시간 초과 (Market {m_type}): {self.REQUEST_TIMEOUT}초")
            except Exception as e:
                print(f"KIND Fetch Exception (Market {m_type}): {e}")

            # 여러 시장을 한 번에 조회할 때만 사이에 지연을 둔다
            if len(targets) > 1:
                await asyncio.sleep(0.2)

        self.last_kind_stats = {
            'rows': len(all_kind_disclosures),               # 이번 호출에서 파싱된 건수
            'rows_recent': sum(self._rows_by_market.values()),  # 시장별 최근값 합계 (감시용)
            'http_ok': http_ok,
            'markets': targets,
            'last_status': last_status,
        }
        return all_kind_disclosures

    def _parse_kind(self, html, market_type_id):
        """KIND HTML 테이블 파싱 (최신 구조 반영)"""
        soup = BeautifulSoup(html, 'html.parser')
        # 최신 KIND는 'list type-00' 클래스를 사용하거나 't7'을 사용함
        rows = soup.select('table.list > tbody > tr') or soup.select('table.t7 > tbody > tr')
        disclosures = []
        
        market_map = {'1': '[유]', '2': '[코]', '3': '[파]'}
        default_market = market_map.get(market_type_id, '')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 5: continue
            
            time_str = cols[0].text.strip()
            corp_name = cols[1].text.strip()
            title = cols[2].text.strip()
            
            # 상세 페이지 링크 추출
            link_node = cols[2].find('a')
            acpt_no = ""
            if link_node and 'onclick' in link_node.attrs:
                # onclick="openDisclsViewer('20240514000123')" 형태에서 번호 추출
                import re
                match = re.search(r"'(.*?)'", link_node['onclick'])
                if match:
                    acpt_no = match.group(1)
            
            # 시장 구분 상세화 (이미지가 있을 경우 우선)
            market = default_market
            img_tag = cols[1].find('img')
            if img_tag and 'alt' in img_tag.attrs:
                alt_text = img_tag['alt']
                if '유가증권' in alt_text: market = '[유]'
                elif '코스닥' in alt_text: market = '[코]'
                elif '코넥스' in alt_text: market = '[넥]'
                elif '파생상품' in alt_text: market = '[파]'
            
            disclosures.append({
                'source': 'KIND',
                'time': time_str,
                'corp_name': corp_name,
                'title': title,
                'id': acpt_no,
                'link': f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={acpt_no}" if acpt_no else "",
                'market': market
            })
        return disclosures

    async def fetch_dart(self, session):
        """DART Open API 공시 목록 조회"""
        if not self.dart_api_key:
            return []
            
        today = datetime.now().strftime('%Y%m%d')
        params = {
            'crtfc_key': self.dart_api_key,
            'bgn_de': today,
            'last_reprt_at': 'Y'
        }
        
        try:
            async with session.get(self.DART_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == '000':
                        return self._parse_dart(data.get('list', []))
                    return []
                else:
                    return []
        except asyncio.TimeoutError:
            print(f"DART 응답 시간 초과: {self.REQUEST_TIMEOUT}초")
            return []
        except Exception as e:
            print(f"DART Fetch Exception: {e}")
            return []

    def _parse_dart(self, data_list):
        """DART JSON 데이터 정규화"""
        disclosures = []
        for item in data_list:
            disclosures.append({
                'source': 'DART',
                'time': item.get('rcept_dt'), # API는 날짜만 줌, 상세 시간은 별도 처리 필요할 수 있음
                'corp_name': item.get('corp_name'),
                'title': item.get('report_nm'),
                'id': item.get('rcept_no'),
                'link': f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no')}",
                'market': "[유]" if item.get('corp_cls') == 'Y' else "[코]" if item.get('corp_cls') == 'K' else ""
            })
        return disclosures
