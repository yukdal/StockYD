# watchdog.py
# 수집기가 '조용히 고장난' 상태를 감지하는 모듈
#
# KIND는 HTML을 파싱해서 공시를 읽어오기 때문에, KRX가 페이지 구조를 바꾸면
# 예외 없이 빈 목록만 계속 반환한다. 이때 프로세스는 멀쩡히 살아 있고 systemd도
# 정상으로 보이지만 실제로는 아무 공시도 탐지하지 못한다. 모니터링 봇에서 가장
# 위험한 실패 형태이므로, 일정 시간 이상 지속되면 텔레그램으로 알린다.

import time


class ScrapeWatchdog:
    """수집 결과를 지켜보다가 이상이 일정 시간 지속되면 경고 신호를 돌려준다.

    record()를 매 수집 주기마다 호출하고, 반환된 신호에 따라 알림을 보내면 된다.
    장 시간에만 호출되는 것을 전제로 한다 (장 마감 시간에는 호출하지 않음).
    """

    # 이상 상태가 이 시간 이상 이어지면 경고 (초)
    DEFAULT_THRESHOLD = 30 * 60          # 30분

    # 경고를 보낸 뒤에도 문제가 계속되면 이 간격으로 다시 알림 (초)
    DEFAULT_REWARN_INTERVAL = 6 * 60 * 60  # 6시간

    # 직전 기록과 이만큼 이상 벌어지면 새로 시작된 것으로 본다 (초).
    # 장 마감 후 다음 날 개장까지의 공백이 이상 지속 시간으로 잘못 누적되는 것을 막는다.
    SESSION_GAP = 60 * 60                 # 1시간

    def __init__(self, threshold=None, rewarn_interval=None):
        self.threshold = threshold if threshold is not None else self.DEFAULT_THRESHOLD
        self.rewarn_interval = rewarn_interval if rewarn_interval is not None else self.DEFAULT_REWARN_INTERVAL

        self._problem_kind = None    # 'empty'(파싱 0건) 또는 'fetch'(수집 실패)
        self._problem_since = None   # 이상이 시작된 시각
        self._last_warned_at = None  # 마지막으로 경고를 보낸 시각
        self._last_record_at = None  # 마지막 기록 시각 (세션 공백 판단용)

    def record(self, row_count, http_ok, now=None):
        """수집 결과 1회를 기록하고 필요한 알림 신호를 반환.

        인자:
            row_count — 이번 주기에 KIND에서 파싱된 전체 공시 건수 (필터링 전)
            http_ok   — 요청 자체가 한 번이라도 성공했는지 여부

        반환: None 또는 (신호, 상세) 튜플
            ('warn', 'empty')   — 파싱 0건이 임계 시간 이상 지속
            ('warn', 'fetch')   — 수집 실패가 임계 시간 이상 지속
            ('recover', 건수)   — 경고 후 정상으로 돌아옴
        """
        now = now if now is not None else time.time()

        # 장 마감 등으로 오래 쉬었다면 이전 상태를 이어받지 않는다
        if self._last_record_at is not None and now - self._last_record_at > self.SESSION_GAP:
            self._reset()
        self._last_record_at = now

        # 현재 상태 판정
        if not http_ok:
            kind = 'fetch'      # 요청이 전부 실패 (네트워크, 차단, 서버 장애)
        elif row_count == 0:
            kind = 'empty'      # 응답은 받았는데 파싱 결과가 0건 (구조 변경 의심)
        else:
            kind = None         # 정상

        # --- 정상 ---
        if kind is None:
            was_warned = self._last_warned_at is not None
            self._reset()
            if was_warned:
                return ('recover', row_count)
            return None

        # --- 이상 ---
        # 이상 종류가 바뀌면 처음부터 다시 센다
        if kind != self._problem_kind:
            self._problem_kind = kind
            self._problem_since = now
            self._last_warned_at = None
            return None

        if now - self._problem_since < self.threshold:
            return None  # 아직 임계 시간에 못 미침 (일시적 현상일 수 있음)

        # 임계 시간을 넘었다 — 첫 경고이거나, 재알림 간격이 지났으면 알린다
        if self._last_warned_at is None or now - self._last_warned_at >= self.rewarn_interval:
            self._last_warned_at = now
            return ('warn', kind)

        return None

    def _reset(self):
        self._problem_kind = None
        self._problem_since = None
        self._last_warned_at = None

    def elapsed_minutes(self, now=None):
        """현재 이상 상태가 지속된 시간(분). 정상이면 0."""
        if self._problem_since is None:
            return 0
        now = now if now is not None else time.time()
        return int((now - self._problem_since) / 60)


def build_warning_message(kind, minutes, last_status=None):
    """경고 신호를 텔레그램 메시지로 변환

    last_status가 있으면 마지막 HTTP 상태 코드를 함께 안내한다.
    403은 KRX가 요청을 거부한 것(차단)이라 조치 방법이 다르므로 따로 구분한다.
    """
    if kind == 'empty':
        return (
            "⚠️ <b>[시스템 경고]</b>\n"
            f"KIND 공시 수집 결과가 <b>{minutes}분째 0건</b>입니다.\n\n"
            "응답은 정상적으로 받고 있으나 공시가 하나도 읽히지 않는 상태입니다. "
            "KIND 페이지 구조가 변경되었을 가능성이 있습니다.\n\n"
            "봇은 계속 동작 중이지만 <b>공시를 탐지하지 못하고 있을 수 있습니다.</b>\n"
            "확인: <code>journalctl -u stock-monitor -n 50</code>"
        )
    if last_status == 403:
        return (
            "🚫 <b>[시스템 경고]</b>\n"
            f"KRX가 요청을 차단했습니다. (<b>HTTP 403</b>, {minutes}분째)\n\n"
            "요청이 너무 잦아 서버 IP가 차단되었을 가능성이 높습니다.\n"
            "봇은 재시도 간격을 늘려가며 대기하지만, <b>공시를 탐지하지 못하는 상태</b>입니다.\n\n"
            "확인: <code>curl -s -o /dev/null -w \'%{http_code}\' https://kind.krx.co.kr/</code>\n"
            "200이 나오면 차단이 풀린 것입니다."
        )

    status_note = f" (마지막 응답: HTTP {last_status})" if last_status else ""
    return (
        "⚠️ <b>[시스템 경고]</b>\n"
        f"KIND 공시 수집이 <b>{minutes}분째 실패</b>하고 있습니다.{status_note}\n\n"
        "네트워크 장애이거나 KIND 서버 문제일 수 있습니다.\n\n"
        "봇은 계속 동작 중이지만 <b>공시를 탐지하지 못하고 있습니다.</b>\n"
        "확인: <code>journalctl -u stock-monitor -n 50</code>"
    )


def build_recovery_message(row_count):
    """정상 복구 알림 메시지"""
    return (
        "✅ <b>[시스템 알림]</b>\n"
        f"KIND 공시 수집이 정상으로 돌아왔습니다. (이번 주기 {row_count}건 수신)"
    )
