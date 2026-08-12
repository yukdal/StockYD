# 주식선물 가격제한폭 확대 실시간 알림 봇

이 프로젝트는 KRX KIND와 DART를 실시간으로 모니터링하여 주식선물 2단계 및 3단계 가격제한폭 확대 공시를 탐지하고 텔레그램으로 즉시 알림을 보냅니다.

## 🛠 주요 기능
- **실시간 모니터링**: KIND(웹 스크래핑)와 DART(API)를 비동기 병렬로 감시.
- **정밀 필터링**: 정규표현식을 이용한 2단계/3단계 및 상승/하락 방향 자동 판별.
- **우선순위 알림**: 3단계 공시 발생 시 최상단 배치 및 시각적 강조.
- **중복 방지**: 고유 ID 및 해시 기반의 중복 알림 차단 로직.

## 🚀 시작하기

### 1. 필수 라이브러리 설치
```bash
pip install aiohttp beautifulsoup4 python-dotenv
```

### 2. 환경변수 설정
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 아래 내용을 입력하십시오.

```env
# DART Open API 인증키
DART_API_KEY=your_dart_api_key_here

# 텔레그램 봇 정보
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# KRX 공식 Open API 인증키 (선택사항 — 없어도 알림은 정상 동작)
KRX_AUTH_KEY=your_krx_auth_key_here
```

### 2-1. KRX 공식 API 인증키 발급 방법 (선택)
1. [KRX Open API 포털](https://openapi.krx.co.kr/) 접속 → 회원가입 (KRX 정보데이터시스템 data.krx.co.kr 계정과 연동)
2. 로그인 후 **인증키(AUTH_KEY) 발급** 신청
3. [서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)에서 **"주식선물(유가) 일별매매정보"**, **"주식선물(코스닥) 일별매매정보"** 각각 **이용 신청** (서비스 단위로 개별 승인 필요)
4. 발급받은 키를 `.env`의 `KRX_AUTH_KEY`에 입력
5. `python test_krx_api.py` 실행하여 연동 확인

> ⚠️ KRX Open API는 **일별(전일) 데이터**만 제공합니다 (다음 영업일 오전 8시 갱신).
> 실시간 공시 감지는 기존 KIND/DART 방식 그대로이며, KRX API는 알림 메시지에
> 해당 종목의 **전일 선물 종가/거래량/미결제약정**을 보강하는 용도입니다.

### 3. 실행
```bash
python stock_monitor.py
```

## 🖥 서버 배포 (OCI)

### 최초 1회: systemd 서비스 등록 (권장)
서버에 접속한 뒤 저장소 디렉토리에서 아래 명령을 실행하십시오.

> ⚠️ 봇을 실행하는 서비스는 **반드시 하나만** 있어야 합니다. 서로 다른 이름의 서비스가 각자 봇을 띄우면 두 인스턴스가 충돌합니다. `install_service.sh`는 이 디렉토리를 관리 중인 다른 서비스가 있으면 설치를 중단하고 안내합니다.

```bash
bash install_service.sh
```

가상환경 생성부터 서비스 등록·기동까지 자동으로 처리하며, **등록 후에는 서버를 재부팅해도 봇이 자동으로 다시 실행**됩니다. 봇이 비정상 종료되어도 10초 뒤 자동 재시작됩니다.

봇을 실행할 계정은 저장소 소유자로 자동 판별됩니다. 직접 지정하려면:

```bash
STOCKYD_USER=ubuntu bash install_service.sh
```

### 코드 변경 후 배포
```bash
bash deploy.sh
```

`git pull` → 의존성 설치 → 재시작 → **기동 검증**까지 수행합니다. systemd 서비스가 등록되어 있으면 `systemctl restart`로, 없으면 기존 `nohup` 방식으로 실행합니다. 봇이 기동에 실패하면 로그를 출력하고 종료 코드 1로 끝나므로 배포 성공 여부를 바로 알 수 있습니다.

### 상태 및 로그 확인
```bash
systemctl status stock-monitor        # 서비스 상태
journalctl -u stock-monitor -f        # 실시간 로그
sudo systemctl restart stock-monitor  # 수동 재시작
```

> systemd를 쓰지 않는 경우(`nohup` 방식) 로그는 `nohup.out`에 쌓이며 `tail -f nohup.out`으로 확인합니다.

### 중복 실행 동작
봇은 `127.0.0.1:51234` 포트로 단일 실행을 보장하며, **먼저 실행된 쪽이 이깁니다.** 이미 봇이 돌고 있으면 새로 실행한 프로세스는 기존 봇을 건드리지 않고 종료 코드 `3`으로 조용히 물러납니다.

> 예전에는 새 프로세스가 기존 봇을 SIGTERM으로 종료시키고 자리를 빼앗았습니다. 이 동작은 자동 재시작(systemd)과 만나면 두 인스턴스가 서로를 죽이고 되살아나는 무한 루프를 만들어, 재시작마다 시작 알림이 발송되는 문제가 있었습니다.

기존 봇을 반드시 교체해야 한다면 명시적으로 켜십시오. **systemd로 관리 중일 때는 사용하지 마십시오.**

```bash
STOCKYD_TAKEOVER=1 ./venv/bin/python stock_monitor.py
```

또한 봇이 반복 재시작되더라도 시작 알림은 10분에 한 번만 발송됩니다 (알림방 도배 방지).

### 전송 실패 시 동작
공시 알림은 놓치면 복구할 수 없으므로, **전송에 성공한 뒤에야 '발송 완료'로 기록**합니다.

- 일시적 실패(네트워크 순단, 텔레그램 `429` 요청 제한, `5xx`)는 채팅방당 최대 3회까지 재시도합니다. `429`의 경우 텔레그램이 알려준 대기 시간을 그대로 지킵니다.
- 3회 모두 실패하면 해당 공시를 기록하지 않으므로, 다음 감시 주기에 다시 후보로 올라와 재시도됩니다. 이때 텔레그램 API를 계속 두드리지 않도록 30초 쉬었다가 진행합니다.
- 봇 차단·채팅방 없음(`400`/`403`/`404`)처럼 재시도해도 달라지지 않는 실패는 로그를 남기고 넘어갑니다. 계속 재시도하면 정상 채팅방에만 중복 발송이 반복되기 때문입니다.

> 예전에는 전송 전에 '본 공시'로 기록했기 때문에, 전송이 실패하면 다음 주기에 중복으로 걸러지면서 해당 알림이 영영 유실됐습니다.

## 📂 파일 구조
- `stock_monitor.py`: 프로그램 실행 진입점 및 메인 루프 (기존 main.py에서 프로세스 충돌 방지를 위해 변경).
- `scraper.py`: KIND 및 DART 데이터 수집 모듈.
- `logic.py`: 키워드 필터링 및 우선순위 정렬 로직.
- `formatter.py`: 텔레그램 메시지 레이아웃 렌더링.
- `notifier.py`: 텔레그램 전송 연동 모듈.
- `krx_api.py`: KRX 공식 Open API 연동 모듈 (전일 주식선물 매매정보).
- `test_krx_api.py`: KRX 인증키 연동 테스트 스크립트.
- `test_telegram.py`: 텔레그램 토큰 유효성 및 채팅방 ID 확인 스크립트 (토큰은 `.env`에서 읽음).
- `deploy.sh`: 서버 배포 스크립트 (git pull → 의존성 설치 → 재시작 → 기동 검증).
- `install_service.sh`: systemd 서비스 등록 스크립트 (재부팅 시 자동 시작 설정).
- `stock-monitor.service`: systemd 유닛 파일 템플릿 (`install_service.sh`가 경로/계정을 채워 설치).
- `requirements.txt`: 의존성 패키지 목록.

## ⚠️ 주의사항
- **API 한도**: DART Open API는 일 10,000건의 호출 제한이 있으므로 폴링 주기를 적절히 유지하십시오 (기본 3초).
- **보안**: `.env` 파일은 절대 공개 저장소에 업로드하지 마십시오. 토큰·인증키를 코드에 직접 적지 말고 반드시 `.env`에서 읽으십시오. 실수로 커밋했다면 파일을 지우는 것만으로는 부족하며(git 히스토리에 남습니다), 해당 키를 즉시 폐기하고 재발급해야 합니다 — 텔레그램은 [@BotFather](https://t.me/BotFather)의 `/revoke`.
