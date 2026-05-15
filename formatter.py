from datetime import datetime

class DisclosureFormatter:
    @staticmethod
    def format_telegram_message(disc):
        """이미지 규격에 맞춘 텔레그램 메시지 렌더링"""
        source_tag = "[KRX 공시]"
        market = disc.get('market', '[미분류]')
        corp_name = disc.get('corp_name', '종목명미상')
        phase = disc.get('phase', '?')
        direction = disc.get('direction', '방향미상')
        time_str = disc.get('time', datetime.now().strftime('%H:%M:%S'))
        link = disc.get('link', '#')
        
        # 상승/하락 구분을 위한 이모지 및 강조
        dir_emoji = "🔴" if direction == "상승" else "🔵" if direction == "하락" else "⚪"
        direction_text = f"<b>{dir_emoji}{direction}</b>"
        
        # 3단계일 경우 이모지와 볼드체 강조 강화
        alert_emoji = "🚨" if phase == 2 else "🔥🚨"
        phase_text = f"<b>{phase}단계</b>"
        
        message = (
            f"{source_tag}\n"
            f"{market}{corp_name} 주식선물 {phase_text} 가격제한폭 확대요건 도달({direction_text}) {alert_emoji}\n\n"
            f"일시: {datetime.now().strftime('%Y-%m-%d')} {time_str}\n"
            f"링크: <a href='{link}'>상세보기</a>"
        )
        return message
