from datetime import datetime, timezone, timedelta

class DisclosureFormatter:
    @staticmethod
    def format_telegram_message(disc, krx_info=None):
        """이미지 규격에 맞춘 텔레그램 메시지 렌더링 (krx_info가 있으면 전일 매매정보 추가)"""
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        source_tag = "[KRX 공시]"
        market = disc.get('market', '[미분류]')
        corp_name = disc.get('corp_name', '종목명미상')
        phase = disc.get('phase', '?')
        direction = disc.get('direction', '방향미상')
        time_str = disc.get('time', now_kst.strftime('%H:%M:%S'))
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
            f"일시: {now_kst.strftime('%Y-%m-%d')} {time_str}\n"
            f"링크: <a href='{link}'>상세보기</a> ✨"
        )

        # KRX 공식 API에서 받은 전일 주식선물 매매정보가 있으면 메시지 하단에 추가
        if krx_info:
            base_date = krx_info.get('base_date', '')
            # YYYYMMDD → YYYY-MM-DD 형태로 보기 좋게 변환
            date_fmt = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}" if len(base_date) == 8 else base_date

            def _num(value, sign=False):
                """'280000.00' 같은 문자열을 '280,000'처럼 읽기 쉽게 변환 (sign=True면 +/- 부호 표시)"""
                try:
                    n = float(str(value).replace(',', ''))          # 문자열 → 숫자
                    formatted = f"{n:+,.0f}" if sign else f"{n:,.0f}"  # 천 단위 쉼표, 소수점 제거
                    return formatted
                except (ValueError, TypeError):
                    return str(value)                               # 변환 실패 시 원본 그대로

            # 연속 공백 제거: '삼성전자   F 202608 ' → '삼성전자 F 202608'
            name = " ".join(str(krx_info.get('name', '-')).split())
            message += (
                f"\n\n📊 <b>[KRX 공식] 전일({date_fmt}) 선물 매매정보</b>\n"
                f"종목: {name}\n"
                f"종가: {_num(krx_info.get('close', '-'))} (전일대비 {_num(krx_info.get('change', '-'), sign=True)})\n"
                f"거래량: {_num(krx_info.get('volume', '-'))} / 미결제약정: {_num(krx_info.get('open_interest', '-'))}"
            )
        return message
