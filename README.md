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

## 📂 파일 구조
- `stock_monitor.py`: 프로그램 실행 진입점 및 메인 루프 (기존 main.py에서 프로세스 충돌 방지를 위해 변경).
- `scraper.py`: KIND 및 DART 데이터 수집 모듈.
- `logic.py`: 키워드 필터링 및 우선순위 정렬 로직.
- `formatter.py`: 텔레그램 메시지 레이아웃 렌더링.
- `notifier.py`: 텔레그램 전송 연동 모듈.
- `krx_api.py`: KRX 공식 Open API 연동 모듈 (전일 주식선물 매매정보).
- `test_krx_api.py`: KRX 인증키 연동 테스트 스크립트.

## ⚠️ 주의사항
- **API 한도**: DART Open API는 일 10,000건의 호출 제한이 있으므로 폴링 주기를 적절히 유지하십시오 (기본 3초).
- **보안**: `.env` 파일은 절대 공개 저장소에 업로드하지 마십시오.
