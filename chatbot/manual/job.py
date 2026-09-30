"""
백그라운드 작업 시작/조회/정리 — 옵션생성·최상위 메뉴 추천을 스레드로 실행 (동시에 1개).
"""

import threading
import time

from core.database import get_connection

from chatbot.manual.category import suggest_categories
from chatbot.manual.generate import _run_generation
from chatbot.manual.progress import JOB_TITLES, _Reporter, _job_lock, _new_job, state


_JOB_RUNNERS = {
    "generate": (JOB_TITLES["generate"], lambda rep: _run_generation(rep)),
    "suggest": (JOB_TITLES["suggest"], lambda rep: suggest_categories(rep)),
}


def _run_job(job: dict) -> None:
    rep = _Reporter(job)
    title, runner = _JOB_RUNNERS[job["kind"]]
    try:
        result = runner(rep)
        with _job_lock:
            job["result"] = result
            job["percent"] = 100
            job["status"] = "done"
    except Exception as e:
        print(f"⚠ {title} 실패: {e}")
        rep.close_running("failed")
        rep.fail("error", f"오류: {e}")
        with _job_lock:
            job["error"] = str(e)
            job["status"] = "error"
    finally:
        with _job_lock:
            job["finishedAt"] = time.time()


def get_generate_job_status() -> dict:
    """현재(또는 마지막) AI 옵션생성 작업 상태. 새로고침 후 화면 복원용."""
    with _job_lock:
        job = state.job
        if job.get("status") == "idle":
            return {"status": "idle"}
        end = job["finishedAt"] or time.time()
        return {
            "kind": job["kind"],
            "status": job["status"],
            "percent": job["percent"],
            "logs": [{"key": x["key"], "message": x["message"], "state": x["state"]} for x in job["logs"]],
            "elapsedSec": int(end - job["startedAt"]),
            "result": job["result"],
            "error": job["error"],
        }


def _start_job(kind: str) -> dict:
    """
    백그라운드 작업 시작. 옵션생성/추천 중 무엇이든 이미 진행 중이면 새로 시작하지 않고
    진행 중인 작업 상태를 그대로 돌려줍니다(kind로 어떤 작업인지 구분).
    """
    with _job_lock:
        if state.job.get("status") == "running":
            already = True
        else:
            already = False
            state.job = _new_job(kind)
            job = state.job
    if not already:
        threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"chatmenu-{kind}").start()
    return get_generate_job_status()


def start_generate_job() -> dict:
    """AI 옵션생성을 백그라운드로 시작합니다."""
    return _start_job("generate")


def regenerate_all_docs() -> dict:
    """
    [매뉴얼 전체 다시 생성] — 모든 문서를 미반영(UPDATEYN='N')으로 되돌리고 옵션생성을 시작합니다.
    내부 정보 제외·긴 제목 요약 같은 규칙이 바뀌었을 때 기존 AI 메뉴를 한 번에 다시 만들 때 사용.
    각 문서로 만든 AI 메뉴만 교체되며, 관리자가 직접 만든 메뉴는 그대로입니다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("UPDATE ATTACH_MANUAL SET UPDATEYN = 'N'")
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    return start_generate_job()


def start_suggest_job() -> dict:
    """최상위 메뉴 AI 추천을 백그라운드로 시작합니다(로컬 CPU에서 1~2분)."""
    return _start_job("suggest")


def clear_generate_job() -> dict:
    """완료/실패한 작업 기록을 지웁니다(관리자가 진행 패널을 닫을 때). 진행 중이면 무시."""
    with _job_lock:
        if state.job.get("status") != "running":
            state.job = {"status": "idle"}
    return get_generate_job_status()
