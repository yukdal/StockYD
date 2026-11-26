# OCI 공인 IP 교체 런북 (KRX 403 차단 대응)

KRX(KIND)가 서버 공인 IP를 차단해 봇이 공시를 수집하지 못할 때, OCI 인스턴스의 공인 IP를 바꿔 복구하는 절차입니다.
위에서부터 순서대로 따라 하면서 체크박스를 채우십시오.

## 배경

| 항목 | 내용 |
| --- | --- |
| 봇 | 한국 주식선물 가격제한폭 확대 공시를 KIND에서 수집해 텔레그램으로 알림 |
| 서버 | OCI ARM 인스턴스 `high-spec-arm`, 계정 `ubuntu` |
| 설치 경로 | `~/stock-monitor` |
| 서비스 | systemd `stock-monitor` |
| 차단 감시 cron | `*/30 * * * * /home/ubuntu/stock-monitor/check_kind_access.sh >> /home/ubuntu/stock-monitor/kind_access.log 2>&1` |
| 감시 상태 파일 | `~/stock-monitor/.kind_access_state` (형식: `<상태코드> <연속성공횟수>`) |

**사고 경위:** 2026년 8월, 예전 설정(3초 주기 × 시장 3개, 분당 약 40회)으로 요청한 결과 KRX가 서버 공인 IP를 HTTP 403으로 차단했습니다. 3.5개월이 지나도 풀리지 않았고, 같은 시각 가정 회선에서는 KIND가 정상적으로 열렸습니다. 따라서 **IP 단위 차단**으로 판단했고, 공인 IP 교체로 해결했습니다.

### ⚠️ 같은 서버에서 도는 다른 봇

이 서버에는 이 프로젝트와 무관한 봇 2개가 함께 돌고 있습니다. **IP 교체는 서버 전체에 영향을 줍니다.**

| 봇 | 실행 파일 | 서비스 |
| --- | --- | --- |
| KStockDB | `~/KStockDB/main.py` | `kstockdb.service` |
| stock_bot | `~/stock_bot/.../swing_main.py` | `stock_bot.service` |

> 🚫 **절대 `pkill -f main.py`를 쓰지 마십시오.** KStockDB(`~/KStockDB/main.py`)까지 함께 죽습니다.
> 이 봇은 반드시 `sudo systemctl stop stock-monitor`로만 멈추십시오.

---

## 1. 언제 쓰나

아래 조건을 **모두** 만족할 때만 IP 교체를 진행합니다.

- [ ] 서버에서 KIND POST 요청이 **지속적으로** `403`이다 (한두 번이 아니라 여러 시간·여러 날에 걸쳐).
  - `kind_access.log`에 403이 계속 쌓여 있는지 확인: `tail -n 50 ~/stock-monitor/kind_access.log`
  - 차단 중에도 간헐적으로 `200`이 섞여 나올 수 있습니다. 한 번의 200을 '해제'로 보지 마십시오.
- [ ] 같은 시각, **가정 회선 또는 휴대폰 데이터에서는 KIND가 열린다** (아래 대조 실험).
- [ ] 차단된 지 충분히 오래되었다 (일시적 차단은 기다리면 풀릴 수 있음).

### 대조 실험 방법

서버와 다른 네트워크에서 같은 사이트가 열리는지 확인해, 문제가 "서버 IP"인지 "KRX 쪽 정책/장애"인지 가릅니다.

- [ ] **휴대폰 브라우저**(Wi-Fi 끄고 모바일 데이터)로 https://kind.krx.co.kr/ 를 열어 정상 표시되는지 확인
- [ ] 또는 **가정 PC**에서 curl로 확인 (서버에서 쓰는 것과 같은 요청):

```bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
EP="https://kind.krx.co.kr/disclosure/todaydisclosure.do"
curl -s -o /dev/null -w "KIND POST: %{http_code}\n" --max-time 20 -A "$UA" -H "Referer: $EP" --data "method=searchTodayDisclosureSub&currentPageSize=100&pageIndex=1&orderMode=1&orderStat=D&forward=todaydisclosure_sub&marketType=1" "$EP"
```

| 서버 | 가정/휴대폰 | 판단 |
| --- | --- | --- |
| 403 | 200 | **IP 단위 차단** → 이 런북 진행 |
| 403 | 403 | KRX 정책 변경·장애 가능성 → IP 교체로 해결 안 됨, 중단 |
| 200 | 200 | 차단 아님 → 봇 로그에서 다른 원인 확인 |

> 확인은 몇 번으로 충분합니다. 같은 요청을 반복해서 두드리지 마십시오.

---

## 2. 사전 점검

- [ ] **현재 공인 IP 기록** (롤백·비교용):

```bash
curl -s ifconfig.me; echo
```

  기록: `이전 IP = ____________________`

- [ ] **이 봇만 정지** (차단 중 요청을 계속 보내지 않도록):

```bash
sudo systemctl stop stock-monitor
systemctl is-active stock-monitor   # inactive 여야 함
```

- [ ] **OCI 콘솔의 Cloud Shell / Console connection 사용 가능 여부 확인**
  - 공인 IP를 떼는 순간 SSH가 끊깁니다. 새 IP 할당이 실패하면 서버에 들어갈 길은 콘솔뿐입니다.
  - 인스턴스 상세 화면의 Console connection(또는 Cloud Shell)이 열리는지 미리 확인해 두십시오.
- [ ] **다른 봇(KStockDB, stock_bot)이 인바운드 IP에 의존하는지 확인**
  - 텔레그램 웹훅, 외부 서비스의 IP 화이트리스트, DNS A 레코드 등에 현재 IP가 등록되어 있다면 교체 후 함께 갱신해야 합니다.
  - 폴링 방식(아웃바운드 요청만)이라면 영향 없음.
  - 의존 항목 메모: `____________________`
- [ ] 다른 봇의 현재 상태 기록:

```bash
systemctl is-active kstockdb stock_bot
```

- [ ] (가능하면) 다른 봇 사용자가 있다면 잠시 끊긴다고 미리 알림

---

## 3. OCI 콘솔 절차

> ⚠️ **공인 IP를 떼는 순간 SSH가 끊깁니다.** 서버 쪽 작업(2단계)을 모두 끝낸 뒤 진행하십시오.
> ℹ️ 아래는 **새 OCI 콘솔(상단 탭 방식)** 기준입니다. 메뉴·탭 이름은 콘솔 버전에 따라 다를 수 있으니, 보이지 않으면 비슷한 이름을 찾으십시오.

- [ ] **Compute → Instances** → 인스턴스 `high-spec-arm` 클릭
- [ ] 인스턴스 상세 페이지 상단 탭(Details / **Networking** / Storage / Security / …)에서 **Networking** 탭 클릭
- [ ] 아래로 스크롤해 **Attached VNICs** 표에서 **VNIC 이름(링크)** 클릭
  - ⚠️ 위쪽의 "Primary VNIC" 요약 영역(Public IPv4 address 등 표시)이 아니라, 그 아래 **Attached VNICs 표**의 링크입니다.
- [ ] VNIC 상세 페이지에서 IP 관련 탭(예: **IP administration**)을 열고 **IPv4 addresses** 표를 찾기
  - 이 탭 이름은 콘솔 버전에 따라 다를 수 있습니다.
- [ ] 기본 사설 IP(Primary private IP) 행의 **⋮** → **Edit**
- [ ] **Public IP type** 에서 현재 유형 확인: `Ephemeral` / `Reserved` → 아래 A 또는 B로 진행

> **구 UI인 경우(왼쪽 Resources 메뉴 방식):** 인스턴스 상세 → 왼쪽 Resources의 **Attached VNICs** → VNIC 이름 클릭 → 왼쪽 Resources의 **IPv4 Addresses** → 기본 사설 IP 행 ⋮ → **Edit**.

### A. 현재 Ephemeral(임시) 공인 IP인 경우

- [ ] Public IP type을 **`No public IP`** 로 바꾸고 **Update** (이 순간 SSH 끊김)
- [ ] 다시 ⋮ → **Edit**
- [ ] Public IP type을 **`Ephemeral public IP`** 로 바꾸고 **Update**
- [ ] 화면에 표시된 **새 공인 IP** 기록: `새 IP = ____________________`
- [ ] 새 IP가 이전 IP와 **다른지** 확인 (같으면 위 과정을 반복)

### B. 현재 Reserved(예약) 공인 IP인 경우

예약 IP는 떼었다 붙여도 같은 IP가 돌아오므로, 다음 중 하나를 선택합니다.

- [ ] **방법 1:** Public IP type을 `No public IP` → `Ephemeral public IP` 로 전환 (예약 IP는 계정에 남음)
- [ ] **방법 2:** **Networking → Reserved public IPs**에서 새 예약 IP를 생성한 뒤 이 VNIC에 할당
  - 💰 사용하지 않는 예약 IP는 요금이 발생할 수 있고, 계정별 예약 IP 개수 한도가 있습니다. 차단된 예전 예약 IP는 필요 없으면 해제하십시오.
- [ ] 새 공인 IP 기록: `새 IP = ____________________`

---

## 4. 사후 확인

새 IP로 SSH 접속한 뒤 진행합니다.

- [ ] SSH 클라이언트의 접속 주소를 새 IP로 갱신 (`~/.ssh/config`의 `HostName`, PuTTY/Termius 세션 등)
- [ ] `known_hosts` 경고가 뜨면: 호스트 키는 그대로이고 주소만 바뀐 것이므로, 이전 IP 항목을 지우고 다시 접속

```bash
# 내 PC에서 실행
ssh-keygen -R <이전 IP>
ssh-keygen -R <새 IP>    # 새 IP를 예전에 다른 서버가 쓰던 경우 대비
ssh ubuntu@<새 IP>
```

> "REMOTE HOST IDENTIFICATION HAS CHANGED"가 **새 IP에서 이전 IP 항목 삭제 후에도** 계속 뜬다면, 실제로 다른 서버일 수 있으니 콘솔에서 IP를 다시 확인하십시오.

### 서버에서 확인 (복붙용)

- [ ] **새 IP 확인 + KIND POST가 200인지 확인** (봇과 동일한 요청):

```bash
curl -s ifconfig.me; echo

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
EP="https://kind.krx.co.kr/disclosure/todaydisclosure.do"
curl -s -o /dev/null -w "KIND POST: %{http_code}\n" --max-time 20 -A "$UA" -H "Referer: $EP" --data "method=searchTodayDisclosureSub&currentPageSize=100&pageIndex=1&orderMode=1&orderStat=D&forward=todaydisclosure_sub&marketType=1" "$EP"
```

  - `ifconfig.me` 결과가 3단계에서 기록한 새 IP와 같아야 함
  - `KIND POST: 200` 이어야 함 → `403`이면 **5. 롤백/실패 시**로 이동

- [ ] **차단 감시 상태 초기화 후 봇 재배포**:

```bash
rm -f ~/stock-monitor/.kind_access_state
cd ~/stock-monitor && bash deploy.sh
```

  - 상태 파일을 지우지 않으면 이전 IP의 `403` 기록이 남아, 다음 cron 실행 때 상태 판단이 꼬일 수 있습니다.

- [ ] **봇 로그 확인** (약 90초 후):

```bash
journalctl -u stock-monitor -n 20 --no-pager
```

  - [ ] 워밍업 메시지 3줄이 보임: `(남은 워밍업 2주기)` → `(남은 워밍업 1주기)` → `(남은 워밍업 0주기)`
  - [ ] `403` / `KIND 수집 실패` 로그가 없음

- [ ] **다른 봇이 정상인지 확인**:

```bash
systemctl is-active kstockdb stock_bot   # 둘 다 active 여야 함
```

- [ ] 2단계에서 메모한 인바운드 의존 항목(웹훅·화이트리스트·DNS)을 새 IP로 갱신
- [ ] 30분 뒤 cron 결과 확인: `tail -n 5 ~/stock-monitor/kind_access.log` 에 200이 기록되는지

---

## 5. 롤백 / 실패 시

### 새 IP에서도 403이 나는 경우

- [ ] **즉시 봇 정지** (차단을 연장시키지 않도록):

```bash
sudo systemctl stop stock-monitor
```

- [ ] **대조 실험 재확인** (1단계 방법 그대로):
  - 가정 회선/휴대폰은 200 → 새 IP도 이미 차단 목록 대역에 있을 수 있음. 시간을 두고 다시 확인하거나 IP를 한 번 더 교체.
  - **가정 회선/휴대폰도 403** → KRX 정책 변경(해외·클라우드 대역 차단, 요청 형식 변경 등) 가능성. **IP 교체로는 해결되지 않습니다.** 더 교체하지 말고 원인부터 조사하십시오.
- [ ] 봇은 정지 상태로 두고, `check_kind_access.sh` cron이 상태 변화를 알려줄 때까지 기다림

### 새 IP 할당 자체가 실패한 경우 (SSH 접속 불가)

- [ ] OCI 콘솔에서 VNIC의 Public IP type을 다시 `Ephemeral public IP`로 설정
- [ ] 그래도 안 되면 Console connection / Cloud Shell로 접속해 네트워크 상태 확인
- [ ] 다른 봇(kstockdb, stock_bot)이 정상 기동했는지 확인

> 참고: Ephemeral IP는 한번 해제하면 **이전 IP로 되돌릴 수 없습니다.** 롤백은 "이전 IP 복원"이 아니라 "공인 IP가 다시 붙은 상태로 복구"를 뜻합니다.

---

## 6. 재차단 방지 — 하지 말 것

새 IP도 같은 방식으로 요청하면 똑같이 차단됩니다. 아래를 지켜 주십시오.

- [ ] `stock_monitor.py`의 **`POLL_INTERVAL`(30초)을 줄이지 않는다.**
- [ ] **시장 순환 조회**(주기당 1개 시장)를 "전 시장 동시 조회"로 되돌리지 않는다.
- [ ] 수집 실패 시 **백오프 설정**(`FETCH_BACKOFF_STEPS`: 60 → 180 → 600 → 1800초)을 줄이거나 끄지 않는다.
- [ ] `debug_krx.py` 등 **디버그 스크립트로 KIND에 반복 요청하지 않는다.** 확인은 위 curl 1회로 충분합니다.
- [ ] **차단 중에는 봇을 재가동하지 않는다.** 차단 상태에서 계속 요청하면 차단이 연장됩니다.
- [ ] **서비스를 중복 생성하지 않는다.** `stock-monitor` 외에 같은 봇을 `nohup`, `screen`, 다른 이름의 서비스로 또 띄우면 요청량이 배가 됩니다.

```bash
# 중복 실행 여부 확인 (stock_monitor.py 프로세스는 1개여야 함)
pgrep -af stock_monitor.py
```

- [ ] 그리고 다시 한 번: **`pkill -f main.py` 금지.**

---

## 7. 관련 문서

- [README — 수집 주기와 요청량](../README.md#수집-주기와-요청량): 폴링 주기, 순환 조회, 워밍업, 백오프, `check_kind_access.sh` cron 등록 방법
- 노션 장애 대응 가이드
