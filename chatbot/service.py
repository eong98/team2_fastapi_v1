"""
chatbot/service.py

AI 상담 흐름 — 시작(인사말), 질문 답변(RAG), 종료 구분선, 관리자 연결 시 대화 요약.
DB 조회·저장은 chat_repository.py, 코드값은 chat_constants.py.
"""

import asyncio
import threading
import time
import traceback

from chatbot.ws_manager import ws_manager
from datetime import datetime
from modules.chat_rag_langgraph import answer_question, llm as chat_llm
from modules.manual_retriever import _get_vectorstore as get_manual_vectorstore
from modules.chat_summary import summarize_chat

from chatbot.chat_constants import AI_GREETING, DIVIDER_AI_END, DIVIDER_AI_START, EMPTY_ANSWER_FALLBACK, ENDFLOW_AI_RESPONDING, ENDFLOW_NEEDS_ADMIN, ENDFLOW_SUMMARIZING, MTYPE_AI_ANSWER, MTYPE_FREE_TEXT, MTYPE_MENU_SELECT, MTYPE_SYSTEM_NOTICE, SENDER_AI, SENDER_SYSTEM, SENDER_USER
from chatbot.chat_repository import ChatRequestError, save_title, create_chat_log, ensure_session_open, get_ai_chat_history, get_chat_conversation, get_session_owner, save_title_and_clear_endflow, set_cmode, update_endflow


def summarize_conversation(sno: str) -> dict:
    """
    세션 대화 전체를 AI로 요약 — { title, content, type }.
    관리자 문의, 종료 후 제목 요약, [이전 내용으로 다시 문의하기]가 모두 이 함수를 거친다.
    (추후 요약 결과를 별도 테이블에 저장할 때는 여기서 저장하면 모든 경로에 적용됨)
    """
    conversation = get_chat_conversation(sno)
    if not conversation:
        raise ChatRequestError(f"세션 {sno}의 대화 로그가 없습니다.")
    return summarize_chat(conversation)


async def summarize_title_in_background(sno: str) -> None:
    """
    상담 종료 후 채팅 목록 제목(STITLE)을 AI 요약으로 바꿈 — 백그라운드 실행(사용자 대기 없음).
    진행 상태(ENDFLOW)는 건드리지 않는다: 관리자 문의용 요약(ENDFLOW=5)과 섞이면 재진입 시
    문의 작성 화면으로 넘어가 버리기 때문. 끝나면 WebSocket으로 알려 목록 제목을 새로고침한다.
    """
    try:
        result = await asyncio.to_thread(summarize_conversation, sno)
        await asyncio.to_thread(save_title, sno, result["title"])
        mno, gno = await asyncio.to_thread(get_session_owner, sno)
        await ws_manager.notify(mno, gno, {"type": "session_updated", "sno": sno})
    except Exception:
        print(f"⚠ 상담 종료 제목 요약 실패(sno={sno}) — 기존 제목 유지")
        traceback.print_exc()


def summarize_and_save(sno: str) -> dict:
    """
    세션의 대화 전체를 요약하고, CHAT_SESSION.STITLE에 즉시 저장합니다.
    title은 채팅목록 타이틀로 저장하며, content/type은 응답으로만 반환합니다.
    """
    update_endflow(sno, ENDFLOW_SUMMARIZING)  # 시작 전에 상태 업데이트

    try:
        result = summarize_conversation(sno)
        save_title_and_clear_endflow(sno, result["title"])  # 성공 시 STITLE 저장 및 ENDFLOW 해제

        return result

    except Exception:
        update_endflow(sno, None)  # 실패 시 대기상태 해제 (무한 로딩 방지)
        raise


WARMUP_MIN_INTERVAL_SEC = 60  # 여러 사람이 동시에 AI 상담을 시작해도 1분에 한 번만 미리 올림
_warmup_lock = threading.Lock()
_last_warmup_at = 0.0


def warm_up_ai_models() -> None:
    """
    AI 상담 시작 직후 백그라운드로 실행 — 첫 질문에서 쓸 모델을 GPU에 미리 올려 둔다.
    인사말을 고정 문구로 바꾸면서 시작 때 LLM을 안 부르게 되어, 모델이 내려가 있으면
    첫 질문 답변이 모델 로딩 시간(수십 초)만큼 늦어지는 문제를 막기 위함.
      - 대화 LLM(gemma): 1토큰만 생성하는 짧은 요청
      - 임베딩(bge-m3): 첫 질문의 매뉴얼 검색용
    사용자 응답과 무관하게 실패해도 무시(로그만).
    """
    global _last_warmup_at
    with _warmup_lock:
        if time.time() - _last_warmup_at < WARMUP_MIN_INTERVAL_SEC:
            return
        _last_warmup_at = time.time()

    started = time.time()
    try:
        chat_llm.model_copy(update={"num_predict": 1, "format": None}).invoke("hi")
        get_manual_vectorstore()._embedding_function.embed_query("워밍업")
        print(f"🔥 AI 모델 미리 올림 완료 ({time.time() - started:.1f}s)")
    except Exception:
        print("⚠ AI 모델 미리 올리기 실패 (첫 질문에서 로딩됨)")
        traceback.print_exc()


def start_ai_consult(sno: str) -> dict:
    """
    AI 상담을 시작합니다.
    1. 'AI 상담' 선택 로그 및 시작 구분선 저장
    2. CMODE=1(AI 상담) 전환
    3. 고정 인사말(AI_GREETING) 저장 — LLM을 부르지 않으므로 즉시 응답
       (예전엔 인사말을 매번 LLM으로 만들어, 모델이 GPU에 올라가 있지 않으면 시작만 수십 초 걸렸음.
        LLM 호출이 없어져서 "답변 생성 중(ENDFLOW=6)" 상태도 거치지 않는다)
    """
    ensure_session_open(sno)
    create_chat_log(sno, SENDER_USER, MTYPE_MENU_SELECT, "AI 상담")
    create_chat_log(sno, SENDER_SYSTEM, MTYPE_SYSTEM_NOTICE, DIVIDER_AI_START)
    set_cmode(sno, 1)
    greeting = AI_GREETING
    greeting_log_no = create_chat_log(sno, SENDER_AI, MTYPE_AI_ANSWER, greeting)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "logs": [
            {"no": greeting_log_no, "sender": SENDER_AI, "mtype": MTYPE_AI_ANSWER, "content": greeting, "cno": None, "cdate": now},
        ],
    }


def end_ai_consult_divider(sno: str) -> dict:
    """AI 상담 종료 구분선만 DB에 저장합니다."""
    log_no = create_chat_log(sno, SENDER_SYSTEM, MTYPE_SYSTEM_NOTICE, DIVIDER_AI_END)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "logs": [
            {"no": log_no, "sender": SENDER_SYSTEM, "mtype": MTYPE_SYSTEM_NOTICE, "content": DIVIDER_AI_END, "cno": None, "cdate": now},
        ],
    }


async def process_ai_chat(sno: str, message: str) -> dict:
    """
    사용자 질의를 처리하여 LLM 답변을 생성 및 DB에 저장합니다.
    DB 조회/저장과 LLM 호출은 모두 동기(블로킹)라서 별도 스레드에서 실행합니다.
    (async 함수 안에서 그대로 부르면 LLM이 답하는 수십 초 동안 서버 전체가 멈춰서,
     다른 사용자의 요청·WebSocket·옵션생성 진행률 조회까지 모두 대기하게 됨)
    """
    result = await asyncio.to_thread(_process_ai_chat_sync, sno, message)

    mno, gno = await asyncio.to_thread(get_session_owner, sno)
    await ws_manager.notify(mno, gno, {"type": "new_message", "sno": sno})
    return result


def _process_ai_chat_sync(sno: str, message: str) -> dict:
    message = (message or "").strip()
    if not message:
        raise ChatRequestError("메시지를 입력해주세요.")
    ensure_session_open(sno)

    history = get_ai_chat_history(sno)

    create_chat_log(sno, SENDER_USER, MTYPE_FREE_TEXT, message)
    update_endflow(sno, ENDFLOW_AI_RESPONDING)  # LLM 호출 시작 직전에 저장

    try:
        try:
            result = answer_question(message, history)
        except Exception:
            # LLM·벡터 검색 실패 — 사용자에겐 안내 문구 + [관리자에게 문의하기], 원인은 서버 로그에 남김
            print(f"⚠ AI 상담 답변 생성 실패(sno={sno}, message={message[:30]!r})")
            traceback.print_exc()
            result = {"answer": "", "needsAdmin": True}
        answer = (result.get("answer") or "").strip()
        needs_admin = bool(result.get("needsAdmin"))
        if not answer:
            # CHAT_LOG.CONTENT는 NOT NULL — 빈 답변이면 안내 문구로 대체하고 관리자 문의 유도
            answer, needs_admin = EMPTY_ANSWER_FALLBACK, True

        ai_log_no = create_chat_log(sno, SENDER_AI, MTYPE_AI_ANSWER, answer)

        update_endflow(sno, ENDFLOW_NEEDS_ADMIN if needs_admin else None)

    except Exception:
        update_endflow(sno, None)  # 예외 발생 시 대기 상태 초기화
        raise

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "logs": [{"no": ai_log_no, "sender": SENDER_AI, "mtype": MTYPE_AI_ANSWER, "content": answer, "cno": None, "cdate": now}],
        "needsAdmin": needs_admin,
    }
