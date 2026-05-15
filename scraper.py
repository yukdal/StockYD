import aiohttp
import asyncio
from datetime import datetime
import json
import os
from bs4 import BeautifulSoup

class DisclosureScraper:
    KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
    DART_URL = "https://opendart.fss.or.kr/api/list.json"

    def __init__(self, dart_api_key=None):
        self.dart_api_key = dart_api_key or os.getenv('DART_API_KEY')
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://kind.krx.co.kr/disclosure/todaydisclosure.do'
        }

    async def fetch_kind(self, session):
        """KIND 오늘의 공시 스크래핑 (코스피, 코스닥, 파생상품 시장)"""
        all_kind_disclosures = []
        # marketType 1: 유가증권, 2: 코스닥, 3: 파생상품
        for m_type in ['1', '2', '3']:
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
                async with session.post(self.KIND_URL, data=payload, headers=self.headers) as response:
                    if response.status == 200:
                        html = await response.text()
                        disclosures = self._parse_kind(html, m_type)
                        all_kind_disclosures.extend(disclosures)
                    else:
                        print(f"KIND Error (Market {m_type}): {response.status}")
            except Exception as e:
                print(f"KIND Fetch Exception (Market {m_type}): {e}")
            
            # 대량 요청 방지 및 안정성을 위한 짧은 지연
            await asyncio.sleep(0.2)
            
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
            async with session.get(self.DART_URL, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == '000':
                        return self._parse_dart(data.get('list', []))
                    return []
                else:
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
