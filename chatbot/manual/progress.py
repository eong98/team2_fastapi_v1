"""
백그라운드 작업 진행 상태 — 진행률·단계 로그 기록기(_Reporter)와 현재 작업 상태.
"""

import threading
import time


class GenerateRunningError(RuntimeError):
    """AI 옵션생성 진행 중에는 매뉴얼 등록/교체/삭제를 막는다(생성 결과와 문서 상태가 어긋나지 않게)."""


# 작업 종류별 이름 (진행 패널 제목, 오류 문구에 사용)
JOB_TITLES = {"generate": "AI 옵션생성", "suggest": "최상위 메뉴 추천"}


# 생성은 요청과 분리된 스레드에서 돌고, 진행 상황은 메모리에 보관한다.
# 화면을 새로고침해도 /status 로 다시 이어서 볼 수 있다(FastAPI 재시작 시에는 초기화).
_job_lock = threading.Lock()


class _JobState:
    """현재(또는 마지막) 백그라운드 작업 1건. 서버 프로세스 메모리에만 있음 (재시작 시 초기화)."""

    def __init__(self):
        self.job: dict = {"status": "idle"}


state = _JobState()


def ensure_not_generating() -> None:
    with _job_lock:
        if state.job.get("status") == "running":
            title = JOB_TITLES.get(state.job.get("kind"), "작업")
            raise GenerateRunningError(f"{title}이 진행 중입니다. 끝난 뒤 다시 시도해주세요.")


class _Reporter:
    """
    진행 로그 기록기.
      step()  : 순차 단계 — 새 단계를 시작하면 직전 단계는 자동으로 완료 처리
      start() / done() / fail() : 병렬 작업(주제별 생성) 각각의 상태
    """

    def __init__(self, job: dict):
        self.job = job
        self._current: str | None = None

    def _set(self, key: str, message: str | None, state: str, percent: float | None) -> None:
        with _job_lock:
            logs = self.job["logs"]
            entry = next((x for x in logs if x["key"] == key), None)
            if entry is None:
                entry = {"key": key, "message": message or "", "state": state}
                logs.append(entry)
            else:
                entry["state"] = state
                if message:
                    entry["message"] = message
            if percent is not None:
                self.job["percent"] = max(self.job["percent"], min(100, round(percent)))

    def step(self, key: str, message: str, percent: float | None = None) -> None:
        if self._current and self._current != key:
            self.done(self._current)
        self._current = key
        self._set(key, message, "running", percent)

    def start(self, key: str, message: str, percent: float | None = None) -> None:
        self._set(key, message, "running", percent)

    def done(self, key: str, message: str | None = None, percent: float | None = None) -> None:
        self._set(key, message, "done", percent)

    def fail(self, key: str, message: str | None = None, percent: float | None = None) -> None:
        self._set(key, message, "failed", percent)

    def info(self, key: str, message: str, percent: float | None = None) -> None:
        """오류가 아닌 안내(예: 자리 부족으로 제외된 섹션) — 화면에서 빨간색 대신 안내 색으로 표시"""
        self._set(key, message, "info", percent)

    def close_running(self, state: str) -> None:
        with _job_lock:
            for entry in self.job["logs"]:
                if entry["state"] == "running":
                    entry["state"] = state


def _new_job(kind: str = "generate") -> dict:
    return {
        "kind": kind,  # generate: AI 옵션생성 / suggest: 최상위 메뉴 추천
        "status": "running",
        "percent": 0,
        "logs": [],
        "startedAt": time.time(),
        "finishedAt": None,
        "result": None,
        "error": None,
    }
