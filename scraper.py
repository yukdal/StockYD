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
        """KIND 오늘의 공시 스크래핑"""
        payload = {
            'method': 'searchTodayDisclosureSub',
            'currentPageSize': '100',
            'pageIndex': '1',
            'orderMode': '1',
            'orderStat': 'D',
            'forward': 'todaydisclosure_sub',
            'searchCodeType': '',
            'searchCorpName': '',
            'all_repnt_code_lst': '',
            'marketType': '0', # 0: 전체
            'dist_repnt_code': '',
            'repnt_code_lst': ''
        }
        
        try:
            async with session.post(self.KIND_URL, data=payload, headers=self.headers) as response:
                if response.status == 200:
                    html = await response.text()
                    return self._parse_kind(html)
                else:
                    print(f"KIND Error: {response.status}")
                    return []
        except Exception as e:
            print(f"KIND Fetch Exception: {e}")
            return []

    def _parse_kind(self, html):
        """KIND HTML 테이블 파싱"""
        soup = BeautifulSoup(html, 'html.parser')
        rows = soup.select('table.t7 > tbody > tr')
        disclosures = []
        
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
            
            market = "[유]" if "KOSPI" in str(cols[1]) else "[코]" if "KOSDAQ" in str(cols[1]) else ""
            # KIND HTML에서는 시장 구분이 class나 이미지로 올 수 있음. 
            # 실제 운영 환경에서는 더 정교한 셀렉터 필요.
            
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
