import re
import hashlib
import os
import json

class DisclosureLogic:
    def __init__(self):
        # 정규표현식: 주식선물 AND 가격제한폭 확대요건 도달 AND (2단계 OR 3단계)
        self.pattern = re.compile(r"주식선물.*([23])단계.*가격제한폭\s*확대요건\s*도달|주식선물.*가격제한폭\s*확대요건\s*도달.*([23])단계")
        self.seen_ids = set()
        self.save_file = "seen_ids.json"
        self.is_first_ever_run = True # 봇 재시작 시 과거 공시 폭탄 발송 방지를 위해 항상 True로 시작
        self._load_seen_ids()

    def _load_seen_ids(self):
        if os.path.exists(self.save_file):
            try:
                with open(self.save_file, "r") as f:
                    data = json.load(f)
                    self.seen_ids = set(data)
            except Exception as e:
                print(f"⚠️ seen_ids 로드 실패: {e}")

    def _save_seen_ids(self):
        try:
            with open(self.save_file, "w") as f:
                json.dump(list(self.seen_ids), f)
        except Exception as e:
            print(f"⚠️ seen_ids 저장 실패: {e}")

    def filter_disclosures(self, disclosures):
        """공시 목록에서 조건에 맞는 항목만 필터링하고 정렬

        ⚠️ 여기서는 seen_ids에 기록하지 않는다.
        기록은 실제로 알림 전송에 성공한 뒤 mark_sent()로 해야 한다.
        (예전에는 이 함수에서 바로 기록했기 때문에, 전송이 실패하면 이미
         '본 공시'로 남아 다음 주기에 중복으로 걸러지면서 알림이 영영 유실됐다.)
        """
        filtered = []
        batch_keys = set()  # 같은 호출 안에서 중복 항목이 두 번 담기지 않도록

        for disc in disclosures:
            title = disc.get('title', '')
            match = self.pattern.search(title)

            if match:
                # 상세 정보 파싱
                # 두 개 이상의 캡처 그룹 중 None이 아닌 것을 선택
                phase_match = match.group(1) or match.group(2)
                phase = int(phase_match) if phase_match else 0
                direction = "상승" if "(상승)" in title else "하락" if "(하락)" in title else "알수없음"

                disc['phase'] = phase
                disc['direction'] = direction
                disc['priority'] = phase # 3단계가 2단계보다 높은 우선순위

                # 고유 해시 생성 (ID가 다르더라도 내용이 같으면 중복 처리)
                disc_hash = self.get_hash(disc)

                # 이미 전송한 공시인지 확인 (ID 기반 및 해시 기반)
                if self._is_seen(disc, disc_hash):
                    continue

                # 같은 배치 안의 중복(시장 구분이 겹쳐 두 번 수집되는 경우 등) 제거
                if disc_hash in batch_keys:
                    continue
                batch_keys.add(disc_hash)

                filtered.append(disc)

        # 우선순위(단계) 내림차순, 그 다음 시간 내림차순 정렬
        filtered.sort(key=lambda x: (x['priority'], x['time']), reverse=True)
        return filtered

    def _is_seen(self, disc, disc_hash=None):
        """이미 전송 완료로 기록된 공시인지 확인"""
        if disc.get('id') and disc['id'] in self.seen_ids:
            return True
        return (disc_hash or self.get_hash(disc)) in self.seen_ids

    def mark_sent(self, disc):
        """알림 전송에 성공한 공시를 '본 공시'로 기록하고 파일에 저장

        전송에 실패한 공시는 기록하지 않으므로, 다음 감시 주기에 다시 후보로
        올라와 자연스럽게 재시도된다.
        """
        if disc.get('id'):
            self.seen_ids.add(disc['id'])
        self.seen_ids.add(self.get_hash(disc))
        self._save_seen_ids()

    def get_hash(self, disc):
        """KIND 등 고유 ID가 불명확할 경우 보조적으로 사용하는 해시 생성"""
        text = f"{disc['time']}_{disc['corp_name']}_{disc['title']}"
        return hashlib.sha256(text.encode()).hexdigest()
