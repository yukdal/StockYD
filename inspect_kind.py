import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def inspect_kind():
    url = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
    payload = {
        'method': 'searchTodayDisclosureSub',
        'currentPageSize': '100',
        'pageIndex': '1',
        'orderMode': '1',
        'orderStat': 'D',
        'forward': 'todaydisclosure_sub',
        'marketType': '0',
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://kind.krx.co.kr/disclosure/todaydisclosure.do'
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload, headers=headers) as response:
            print(f"Status: {response.status}")
            html = await response.text()
            print(f"HTML Length: {len(html)}")
            
            with open("kind_debug.html", "w", encoding="utf-8") as f:
                f.write(html)
            
            print("Saved KIND response to kind_debug.html")
            
            if "table" in html:
                print("Table tag found in HTML")
                if "t7" in html:
                    print("Class 't7' found in HTML")
                else:
                    print("Class 't7' NOT found in HTML. Searching for other table classes...")
                    import re
                    classes = re.findall(r'class="([^"]*)"', html)
                    print(f"Found classes: {set(classes)}")
            else:
                print("Table tag NOT found in HTML")

if __name__ == "__main__":
    asyncio.run(inspect_kind())
