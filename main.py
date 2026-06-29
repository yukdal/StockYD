# 기존 main.py는 OCI 서버 내 다른 프로그램과의 프로세스 이름(pkill -f main.py) 충돌을 방지하기 위해 
# stock_monitor.py로 이전되었습니다.
# 이 파일은 기존 실행 방식과의 호환성을 위해 남겨두었으며, 직접 실행 시 stock_monitor.py를 실행합니다.
import os
import sys

if __name__ == "__main__":
    print("⚠️ main.py 대신 stock_monitor.py를 실행해주세요.")
    print("🚀 stock_monitor.py를 실행합니다...")
    os.system(f"{sys.executable} stock_monitor.py")
