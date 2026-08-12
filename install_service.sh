#!/bin/bash
#
# StockYD systemd 서비스 설치 스크립트
#
# 하는 일:
#   1. 현재 사용자/디렉토리를 자동 감지하여 stockyd.service 템플릿을 채웁니다
#   2. venv가 없으면 생성하고 의존성을 설치합니다
#   3. nohup으로 떠 있던 기존 봇을 정리합니다 (포트 잠금 충돌 방지)
#   4. /etc/systemd/system/stockyd.service 에 설치하고 enable + start
#
# 사용법 (서버에서):
#   bash install_service.sh
#
# 설치 후에는 재부팅해도 봇이 자동으로 다시 실행됩니다.

set -u

SERVICE_NAME="stockyd"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/$SERVICE_NAME.service"
TARGET="/etc/systemd/system/$SERVICE_NAME.service"

echo "🔧 StockYD systemd 서비스 설치를 시작합니다..."
echo "----------------------------------------------------"

# --- 사전 점검 ---------------------------------------------------------------

if [ ! -f "$TEMPLATE" ]; then
    echo "❌ 서비스 템플릿을 찾을 수 없습니다: $TEMPLATE"
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "❌ 이 시스템에는 systemd가 없습니다. install_service.sh를 사용할 수 없습니다."
    echo "👉 기존 방식대로 'bash deploy.sh'로 실행해주세요."
    exit 1
fi

# 봇을 실행할 계정 결정
#   1) STOCKYD_USER 환경변수가 있으면 최우선
#   2) sudo로 실행했다면 sudo를 부른 원래 계정(SUDO_USER)
#   3) 그래도 root라면 저장소 디렉토리의 소유자 (.env를 읽어야 하므로 소유자가 가장 정확)
#   4) 마지막으로 현재 계정
# → 'sudo bash install_service.sh'로 실행해도 User=root로 잘못 설치되지 않습니다.
DIR_OWNER="$(stat -c '%U' "$SCRIPT_DIR" 2>/dev/null || echo '')"
RUN_USER="${STOCKYD_USER:-${SUDO_USER:-$(id -un)}}"
if [ "$RUN_USER" = "root" ] && [ -n "$DIR_OWNER" ] && [ "$DIR_OWNER" != "root" ]; then
    RUN_USER="$DIR_OWNER"
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
    echo "❌ '$RUN_USER' 계정을 찾을 수 없습니다."
    echo "👉 'STOCKYD_USER=<계정명> bash install_service.sh' 형태로 계정을 직접 지정해주세요."
    exit 1
fi

if [ "$RUN_USER" = "root" ]; then
    echo "⚠️ 봇을 root 권한으로 실행하도록 설치합니다."
    echo "👉 일반 계정으로 돌리려면: STOCKYD_USER=<계정명> bash install_service.sh"
fi

# sudo 필요 여부 판단 (root면 sudo 없이 실행)
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
        echo "❌ sudo가 없어 서비스를 설치할 수 없습니다. root로 실행해주세요."
        exit 1
    fi
    SUDO="sudo"
fi

echo "👤 실행 계정 : $RUN_USER"
echo "📂 작업 경로 : $SCRIPT_DIR"

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "⚠️ .env 파일이 없습니다. 봇이 텔레그램 토큰을 읽지 못해 알림을 보내지 못합니다."
    echo "👉 README의 '환경변수 설정'을 참고하여 $SCRIPT_DIR/.env 를 먼저 만들어주세요."
fi

# --- 가상환경 준비 -----------------------------------------------------------

if [ ! -x "$SCRIPT_DIR/venv/bin/python" ]; then
    echo "🐍 가상환경(venv)이 없어 새로 생성합니다..."
    if ! python3 -m venv "$SCRIPT_DIR/venv"; then
        echo "❌ 가상환경 생성에 실패했습니다."
        echo "👉 Ubuntu 계열이라면 'sudo apt install -y python3-venv' 후 다시 시도해주세요."
        exit 1
    fi
fi

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    echo "📦 의존성 패키지를 설치합니다..."
    if ! "$SCRIPT_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"; then
        echo "❌ 패키지 설치에 실패했습니다. 네트워크 상태를 확인해주세요."
        exit 1
    fi
fi

# sudo/root로 설치하면 venv가 root 소유가 되어 이후 일반 계정의 pip 설치가 막힙니다.
# 봇 실행 계정으로 소유권을 넘겨줍니다.
if [ "$(id -u)" -eq 0 ] && [ "$RUN_USER" != "root" ]; then
    chown -R "$RUN_USER" "$SCRIPT_DIR/venv" 2>/dev/null || true
fi

# --- 기존 nohup 프로세스 정리 -------------------------------------------------
# stock_monitor.py는 127.0.0.1:51234 포트로 중복 실행을 막습니다.
# 예전 방식(nohup)으로 떠 있는 봇이 남아 있으면 서비스가 기동하자마자 종료되므로 먼저 정리합니다.

if pgrep -f "stock_monitor.py" >/dev/null 2>&1; then
    echo "🔫 기존에 실행 중인 봇 프로세스를 종료합니다..."
    pkill -f "stock_monitor.py" 2>/dev/null
    for _ in $(seq 1 10); do
        pgrep -f "stock_monitor.py" >/dev/null 2>&1 || break
        sleep 1
    done
    if pgrep -f "stock_monitor.py" >/dev/null 2>&1; then
        echo "   정상 종료되지 않아 강제 종료합니다."
        pkill -9 -f "stock_monitor.py" 2>/dev/null
        sleep 1
    fi
fi

# --- 서비스 파일 생성 및 설치 -------------------------------------------------

echo "📝 서비스 파일을 생성합니다: $TARGET"

TMP_UNIT="$(mktemp)"
# 경로에 '|'가 들어갈 일은 없으므로 sed 구분자로 사용
sed -e "s|__USER__|$RUN_USER|g" \
    -e "s|__WORKDIR__|$SCRIPT_DIR|g" \
    "$TEMPLATE" > "$TMP_UNIT"

# 치환이 남아 있으면 설치 중단 (잘못된 유닛 파일 설치 방지)
if grep -q "__USER__\|__WORKDIR__" "$TMP_UNIT"; then
    echo "❌ 서비스 파일 치환에 실패했습니다."
    rm -f "$TMP_UNIT"
    exit 1
fi

$SUDO install -m 644 "$TMP_UNIT" "$TARGET"
rm -f "$TMP_UNIT"

echo "🔄 systemd 데몬을 다시 읽습니다..."
$SUDO systemctl daemon-reload

echo "⚙️ 부팅 시 자동 시작을 활성화합니다..."
$SUDO systemctl enable "$SERVICE_NAME" >/dev/null

echo "🌟 서비스를 시작합니다..."
$SUDO systemctl restart "$SERVICE_NAME"

# --- 기동 검증 ---------------------------------------------------------------

echo "🔍 정상 기동 여부를 확인합니다 (최대 15초 대기)..."
for _ in $(seq 1 15); do
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        break
    fi
    sleep 1
done

echo "----------------------------------------------------"
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ 설치 완료! 서비스가 정상 실행 중입니다."
    echo "   이제 서버를 재부팅해도 봇이 자동으로 다시 실행됩니다."
    echo ""
    echo "👉 실시간 로그 확인 : journalctl -u $SERVICE_NAME -f"
    echo "👉 상태 확인        : systemctl status $SERVICE_NAME"
    echo "👉 재시작           : sudo systemctl restart $SERVICE_NAME"
    echo "👉 중지 / 자동시작 해제 : sudo systemctl stop $SERVICE_NAME && sudo systemctl disable $SERVICE_NAME"
    echo "----------------------------------------------------"
    exit 0
else
    echo "❌ 서비스가 기동하지 못했습니다. 최근 로그를 출력합니다:"
    echo "----------------------------------------------------"
    $SUDO journalctl -u "$SERVICE_NAME" -n 40 --no-pager
    echo "----------------------------------------------------"
    exit 1
fi
