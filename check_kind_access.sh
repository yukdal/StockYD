#!/bin/bash
#
# KIND 접근 가능 여부 확인 스크립트 (cron용)
#
# KRX가 서버 IP를 차단하면(HTTP 403) 봇이 공시를 전혀 수집하지 못한다.
# 차단이 언제 풀리는지 계속 들여다보기 어려우므로, 주기적으로 확인하다가
# 상태가 바뀌는 순간에만 텔레그램으로 알린다.
#
# 등록 (30분마다):
#   crontab -e
#   */30 * * * * /home/ubuntu/stock-monitor/check_kind_access.sh >> /home/ubuntu/stock-monitor/kind_access.log 2>&1
#
# 상태가 바뀔 때만 알림을 보내므로 매번 실행되어도 알림방이 시끄러워지지 않는다.
# 봇을 자동으로 재시작하지는 않는다. 차단이 풀린 직후 바로 켜기보다는
# 상황을 보고 사람이 판단하는 편이 안전하기 때문이다.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

STATE_FILE="$SCRIPT_DIR/.kind_access_state"
CHECK_URL="${KIND_CHECK_URL:-https://kind.krx.co.kr/}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# --- 접근 확인 ---------------------------------------------------------------
# 페이지 본문은 버리고 상태 코드만 본다 (불필요한 트래픽을 만들지 않기 위함)
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -A "$UA" "$CHECK_URL" 2>/dev/null)"

# curl 자체가 실패하면 000이 반환된다 (DNS 실패, 연결 불가 등)
[ -z "$CODE" ] && CODE="000"

PREV="$(cat "$STATE_FILE" 2>/dev/null || echo "")"

echo "[$TIMESTAMP] KIND 접근 확인: HTTP $CODE (이전: ${PREV:-없음})"

# 상태가 그대로면 조용히 종료
if [ "$CODE" = "$PREV" ]; then
    exit 0
fi

echo "$CODE" > "$STATE_FILE"

# --- 텔레그램 알림 -----------------------------------------------------------

send_telegram() {
    local text="$1"

    if [ ! -f "$SCRIPT_DIR/.env" ]; then
        echo "  ⚠️ .env가 없어 알림을 보내지 못했습니다."
        return 1
    fi

    local token chat_raw
    token="$(grep -E '^[[:space:]]*TELEGRAM_BOT_TOKEN=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d "\"' ")"
    chat_raw="$(grep -E '^[[:space:]]*TELEGRAM_CHAT_ID=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d "\"'")"

    if [ -z "$token" ] || [ -z "$chat_raw" ]; then
        echo "  ⚠️ .env에서 토큰 또는 채팅방 ID를 읽지 못했습니다."
        return 1
    fi

    # TELEGRAM_CHAT_ID는 쉼표로 여러 개가 들어올 수 있다
    local sent=0
    for chat in $(echo "$chat_raw" | tr ',' ' '); do
        [ -z "$chat" ] && continue
        if curl -s -o /dev/null --max-time 15 \
            -d "chat_id=$chat" \
            -d "parse_mode=HTML" \
            --data-urlencode "text=$text" \
            "https://api.telegram.org/bot${token}/sendMessage"; then
            sent=$((sent + 1))
        fi
    done
    echo "  📨 알림 전송: ${sent}개 채팅방"
}

if [ "$CODE" = "200" ]; then
    # 차단 해제 (또는 최초 실행인데 접근 가능)
    echo "  ✅ KIND 접근이 가능해졌습니다."
    send_telegram "✅ <b>[시스템 알림]</b>
KIND 접근이 <b>정상으로 돌아왔습니다.</b> (HTTP 200)

봇을 다시 시작할 수 있습니다:
<code>cd ~/stock-monitor && bash deploy.sh</code>

다시 차단되지 않도록 요청 주기 설정은 그대로 두십시오."

elif [ -n "$PREV" ] && [ "$PREV" = "200" ]; then
    # 정상이었다가 막힌 경우에만 알린다
    echo "  🚫 KIND 접근이 차단되었습니다."
    if [ "$CODE" = "403" ]; then
        send_telegram "🚫 <b>[시스템 알림]</b>
KRX가 접근을 <b>차단했습니다.</b> (HTTP 403)

봇을 멈추는 편이 회복에 유리합니다:
<code>sudo systemctl stop stock-monitor</code>

차단이 풀리면 이 스크립트가 다시 알려줍니다."
    else
        send_telegram "⚠️ <b>[시스템 알림]</b>
KIND 접근에 문제가 생겼습니다. (HTTP $CODE)

네트워크 장애이거나 KIND 서버 문제일 수 있습니다."
    fi

else
    # 최초 실행이면서 접근 불가인 경우 — 이미 아는 상태이므로 알리지 않는다
    echo "  ℹ️ 접근 불가 상태를 기록했습니다. (알림 없음)"
fi

exit 0
