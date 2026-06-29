#!/bin/bash

echo "🚀 OCI 서버 자동 배포 및 재실행 스크립트 시작..."

# 1. Git 최신 코드 업데이트
echo "📦 Git 최신 코드 가져오는 중 (git pull)..."
git pull origin main

# 2. 가상환경 및 의존성 패키지 점검
if [ -d "venv" ]; then
    echo "🐍 가상환경(venv) 감지됨. 패키지 의존성 확인 중..."
    if [ -f "requirements.txt" ]; then
        ./venv/bin/pip install -r requirements.txt
    fi
else
    echo "⚠️ 가상환경(venv)이 감지되지 않았습니다."
    echo "👉 초기 설치인 경우: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt 를 먼저 실행해주세요."
fi

# 3. 기존 실행 중인 봇 프로세스 안전 종료
echo "🔫 기존 실행 중인 봇 프로세스 종료 중..."
pkill -f main.py
sleep 2

# 4. 백그라운드 정상 재실행
echo "🌟 봇 백그라운드 실행 시작 (nohup)..."
nohup ./venv/bin/python main.py > nohup.out 2>&1 &

echo "----------------------------------------------------"
echo "✅ 배포 및 재실행이 완료되었습니다!"
echo "👉 실시간 로그를 확인하시려면 아래 명령어를 입력하세요:"
echo "tail -f nohup.out"
echo "----------------------------------------------------"
