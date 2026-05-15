import re
import hashlib

class DisclosureLogic:
    def __init__(self):
        # 정규표현식: 주식선물 AND 가격제한폭 확대요건 도달 AND (2단계 OR 3단계)
        # 정규표현식: 주식선물 [23]단계 가격제한폭 확대요건 도달
        # 정규표현식: 주식선물 [23]단계 가격제한폭 확대요건 도달 (순서 유연하게 대응)
        self.pattern = re.compile(r"주식선물.*([23])단계.*가격제한폭\s*확대요건\s*도달|주식선물.*가격제한폭\s*확대요건\s*도달.*([23])단계")
        self.seen_ids = set()

    def filter_disclosures(self, disclosures):
        """공시 목록에서 조건에 맞는 항목만 필터링하고 정렬"""
        filtered = []
        for disc in disclosures:
            title = disc.get('title', '')
            match = self.pattern.search(title)
            
            if match:
                # 중복 체크 (ID 기반)
                if disc['id'] in self.seen_ids:
                    continue
                
                # 상세 정보 파싱
                # 두 개 이상의 캡처 그룹 중 None이 아닌 것을 선택
                phase_match = match.group(1) or match.group(2)
                phase = int(phase_match) if phase_match else 0
                direction = "상승" if "(상승)" in title else "하락" if "(하락)" in title else "알수없음"
                
                disc['phase'] = phase
                disc['direction'] = direction
                disc['priority'] = phase # 3단계가 2단계보다 높은 우선순위
                
                filtered.append(disc)
                self.seen_ids.add(disc['id'])
        
        # 우선순위(단계) 내림차순, 그 다음 시간 내림차순 정렬
        filtered.sort(key=lambda x: (x['priority'], x['time']), reverse=True)
        return filtered

    def get_hash(self, disc):
        """KIND 등 고유 ID가 불명확할 경우 보조적으로 사용하는 해시 생성"""
        text = f"{disc['time']}_{disc['corp_name']}_{disc['title']}"
        return hashlib.sha256(text.encode()).hexdigest()
