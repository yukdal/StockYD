#!/bin/bash
#
# OCI 서버 자동 배포 및 재실행 스크립트
#
# systemd 서비스(stockyd)가 설치되어 있으면 systemctl로 재시작하고,
# 없으면 기존 방식대로 nohup 백그라운드로 실행합니다.
# 어느 쪽이든 재실행 후 봇이 실제로 살아있는지 검증한 뒤 종료합니다.
#
# 사용법: bash deploy.sh

set -u

SERVICE_NAME="stockyd"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
PID_FILE="$SCRIPT_DIR/stock_monitor.pid"
LOG_FILE="$SCRIPT_DIR/nohup.out"

echo "🚀 OCI 서버 자동 배포 및 재실행 스크립트 시작..."
echo "📂 작업 경로: $SCRIPT_DIR"
echo "----------------------------------------------------"

# --- 1. Git 최신 코드 업데이트 ------------------------------------------------

echo "📦 Git 최신 코드 가져오는 중 (git pull)..."
if ! git pull origin main; then
    echo "❌ git pull에 실패했습니다. 로컬 변경사항 충돌이나 네트워크 문제일 수 있습니다."
    echo "👉 'git status'로 상태를 확인한 뒤 다시 시도해주세요."
    exit 1
fi

DEPLOYED_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo '-')"
echo "   현재 커밋: $DEPLOYED_COMMIT"

# --- 2. 가상환경 및 의존성 패키지 점검 ----------------------------------------

if [ ! -x "$PYTHON_BIN" ]; then
    echo "🐍 가상환경(venv)이 없어 새로 생성합니다..."
    if ! python3 -m venv "$SCRIPT_DIR/venv"; then
        echo "❌ 가상환경 생성에 실패했습니다."
        echo "👉 Ubuntu 계열이라면 'sudo apt install -y python3-venv' 후 다시 시도해주세요."
        exit 1
    fi
fi

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    echo "📦 의존성 패키지 확인 중..."
    if ! "$SCRIPT_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"; then
        echo "❌ 패키지 설치에 실패했습니다. 네트워크 상태를 확인해주세요."
        exit 1
    fi
else
    echo "⚠️ requirements.txt가 없어 패키지 설치를 건너뜁니다."
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "⚠️ .env 파일이 없습니다. 텔레그램 토큰을 읽지 못해 알림이 전송되지 않습니다."
fi

# --- 3. systemd 서비스 사용 여부 판단 -----------------------------------------
# 서비스가 등록되어 있으면 systemd가 프로세스를 관리하므로 pkill/nohup을 쓰면 안 됩니다.
# (pkill로 죽여도 systemd가 곧바로 되살려 중복 실행 충돌이 발생합니다.)

USE_SYSTEMD=false
if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
        USE_SYSTEMD=true
    fi
fi

# --- 4-A. systemd 방식으로 재시작 ---------------------------------------------

if [ "$USE_SYSTEMD" = true ]; then
    echo "----------------------------------------------------"
    echo "⚙️ systemd 서비스($SERVICE_NAME)가 감지되었습니다. systemctl로 재시작합니다."

    SUDO=""
    [ "$(id -u)" -ne 0 ] && SUDO="sudo"

    if ! $SUDO systemctl restart "$SERVICE_NAME"; then
        echo "❌ 서비스 재시작 명령이 실패했습니다."
        exit 1
    fi

    echo "🔍 정상 기동 여부를 확인합니다 (최대 15초 대기)..."
    for _ in $(seq 1 15); do
        systemctl is-active --quiet "$SERVICE_NAME" && break
        sleep 1
    done

    echo "----------------------------------------------------"
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "✅ 배포 및 재실행이 완료되었습니다! (커밋: $DEPLOYED_COMMIT)"
        echo "👉 실시간 로그 : journalctl -u $SERVICE_NAME -f"
        echo "👉 상태 확인   : systemctl status $SERVICE_NAME"
        echo "----------------------------------------------------"
        exit 0
    else
        echo "❌ 서비스가 기동하지 못했습니다. 최근 로그를 출력합니다:"
        echo "----------------------------------------------------"
        $SUDO journalctl -u "$SERVICE_NAME" -n 40 --no-pager
        echo "----------------------------------------------------"
        exit 1
    fi
fi

# --- 4-B. 기존 nohup 방식으로 재시작 ------------------------------------------

echo "----------------------------------------------------"
echo "ℹ️ systemd 서비스가 등록되어 있지 않아 nohup 방식으로 실행합니다."
echo "👉 재부팅 후 자동 시작을 원하시면 'bash install_service.sh'를 한 번 실행해주세요."

# 기존 실행 중인 봇 프로세스 안전 종료 (SIGTERM → 최대 10초 대기 → SIGKILL)
#
# ⚠️ 반드시 이 프로젝트 경로($SCRIPT_DIR)가 포함된 프로세스만 종료해야 한다.
# 같은 서버에 다른 봇들이 함께 돌고 있어(예: ~/KStockDB/main.py, ~/stock_bot/swing_main.py)
# 'pkill -f main.py' 같은 넓은 패턴을 쓰면 무관한 프로젝트까지 죽인다.
# main.py는 os.system으로 stock_monitor.py를 실행하므로 부모까지 함께 정리한다.
# (pgrep/pkill은 확장 정규식(ERE)을 사용한다)
KILL_PATTERN="$SCRIPT_DIR/.*(stock_monitor\.py|main\.py)"

if pgrep -f "$KILL_PATTERN" >/dev/null 2>&1; then
    echo "🔫 기존 실행 중인 봇 프로세스 종료 중..."
    echo "   (대상: $SCRIPT_DIR 경로의 프로세스만)"
    pkill -f "$KILL_PATTERN" 2>/dev/null

    for _ in $(seq 1 10); do
        pgrep -f "$KILL_PATTERN" >/dev/null 2>&1 || break
        sleep 1
    done

    if pgrep -f "$KILL_PATTERN" >/dev/null 2>&1; then
        echo "   정상 종료되지 않아 강제 종료합니다."
        pkill -9 -f "$KILL_PATTERN" 2>/dev/null
        sleep 1
    fi
    echo "   기존 프로세스 종료 완료."
else
    echo "ℹ️ 실행 중인 기존 봇 프로세스가 없습니다."
fi

# 죽은 프로세스가 남긴 PID 파일 정리 (다음 기동 시 엉뚱한 PID에 SIGTERM 보내는 것 방지)
rm -f "$PID_FILE"

echo "🌟 봇 백그라운드 실행 시작 (nohup)..."
nohup "$PYTHON_BIN" -u stock_monitor.py > "$LOG_FILE" 2>&1 &
BOT_PID=$!

# 기동 검증: 5초 뒤에도 프로세스가 살아있어야 정상
# (중복 실행 잠금이나 import 오류로 즉시 죽는 경우를 여기서 잡습니다)
echo "🔍 정상 기동 여부를 확인합니다 (5초 대기)..."
sleep 5

echo "----------------------------------------------------"
if kill -0 "$BOT_PID" 2>/dev/null; then
    echo "✅ 배포 및 재실행이 완료되었습니다! (PID: $BOT_PID / 커밋: $DEPLOYED_COMMIT)"
    echo "👉 실시간 로그를 확인하시려면 아래 명령어를 입력하세요:"
    echo "   tail -f $LOG_FILE"
    echo "----------------------------------------------------"
    exit 0
else
    echo "❌ 봇이 기동 직후 종료되었습니다. 최근 로그를 출력합니다:"
    echo "----------------------------------------------------"
    tail -n 40 "$LOG_FILE" 2>/dev/null || echo "(로그 파일이 없습니다: $LOG_FILE)"
    echo "----------------------------------------------------"
    exit 1
fi
