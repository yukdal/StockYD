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

# 봇이 실제로 사용하는 요청과 똑같이 보내 확인한다.
# 메인 페이지가 열리는 것과 공시 조회가 되는 것은 별개일 수 있으므로,
# '봇이 지금 동작할 수 있는가'를 직접 확인하는 편이 정확하다.
CHECK_URL="${KIND_CHECK_URL:-https://kind.krx.co.kr/disclosure/todaydisclosure.do}"
CHECK_DATA="method=searchTodayDisclosureSub&currentPageSize=100&pageIndex=1&orderMode=1&orderStat=D&forward=todaydisclosure_sub&marketType=1"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# --- 접근 확인 ---------------------------------------------------------------
# 응답 본문은 버리고 상태 코드만 본다 (불필요한 트래픽을 만들지 않기 위함)
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
    -A "$UA" -H "Referer: $CHECK_URL" --data "$CHECK_DATA" "$CHECK_URL" 2>/dev/null)"

# curl 자체가 실패하면 000이 반환된다 (DNS 실패, 연결 불가 등)
[ -z "$CODE" ] && CODE="000"

# --- 연속 확인 ---------------------------------------------------------------
# ⚠️ 한 번의 200으로 '해제'를 판단하면 안 된다.
#
# 실제로 차단이 유지되는 중에도 간헐적으로 200이 섞여 나오는 것을 확인했다.
# 그 한 번을 보고 해제 알림을 보냈다가, 봇을 재시작하자마자 다시 403을 맞았다.
# 그래서 CONFIRM_RUNS번 연속으로 성공해야 해제로 인정한다.
CONFIRM_RUNS=2

# 상태 파일 형식: "<상태코드> <연속 성공 횟수>"
PREV_RAW="$(cat "$STATE_FILE" 2>/dev/null || echo "")"
PREV_CODE="$(echo "$PREV_RAW" | awk '{print $1}')"
PREV_OK="$(echo "$PREV_RAW" | awk '{print $2}')"
[ -z "$PREV_OK" ] && PREV_OK=0

if [ "$CODE" = "200" ]; then
    if [ "$PREV_CODE" = "200" ]; then
        OK_COUNT=$((PREV_OK + 1))
    else
        OK_COUNT=1
    fi
else
    OK_COUNT=0
fi

echo "[$TIMESTAMP] KIND 접근 확인: HTTP $CODE (이전: ${PREV_CODE:-없음}, 연속 성공 ${OK_COUNT}/${CONFIRM_RUNS})"

echo "$CODE $OK_COUNT" > "$STATE_FILE"

# 아직 확인 횟수를 못 채웠으면 조용히 종료
if [ "$CODE" = "200" ] && [ "$OK_COUNT" -lt "$CONFIRM_RUNS" ]; then
    echo "  ⏳ 접근에 성공했지만 아직 확정하지 않습니다. 다음 확인에서 한 번 더 성공하면 알립니다."
    exit 0
fi

# 이미 알린 상태가 이어지고 있으면 조용히 종료
if [ "$CODE" = "200" ] && [ "$OK_COUNT" -gt "$CONFIRM_RUNS" ]; then
    exit 0
fi
# 접근 불가일 때 '차단됨' 알림은 '해제로 확정했던 상태'에서 무너진 경우에만 보낸다.
# 확정 전의 일시적인 200 뒤에 다시 403이 오는 것은 차단이 이어지는 중일 뿐이므로 알리지 않는다.
if [ "$CODE" != "200" ] && { [ "$PREV_CODE" != "200" ] || [ "$PREV_OK" -lt "$CONFIRM_RUNS" ]; }; then
    echo "  ℹ️ 접근 불가 상태가 이어지고 있습니다. (알림 없음)"
    exit 0
fi

PREV="$PREV_CODE"

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
    # CONFIRM_RUNS번 연속 성공 — 이제 해제로 인정한다
    echo "  ✅ KIND 접근이 가능해졌습니다. (${CONFIRM_RUNS}회 연속 확인)"
    send_telegram "✅ <b>[시스템 알림]</b>
KIND 접근이 <b>정상으로 돌아왔습니다.</b> (HTTP 200, ${CONFIRM_RUNS}회 연속 확인)

봇이 실제로 사용하는 공시 조회 요청으로 확인했습니다.

봇을 다시 시작할 수 있습니다:
<code>cd ~/stock-monitor && bash deploy.sh</code>

재시작 후 <code>journalctl -u stock-monitor -n 20</code>로 403이 없는지 확인하십시오.
다시 차단되지 않도록 요청 주기 설정은 그대로 두십시오."

elif [ "$PREV" = "200" ]; then
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
