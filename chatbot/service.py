"""
chatbot/service.py

AI 상담 흐름 — 시작(인사말), 질문 답변(RAG), 종료 구분선, 관리자 연결 시 대화 요약.
DB 조회·저장은 chat_repository.py, 코드값은 chat_constants.py.
"""

import asyncio
import traceback

from chatbot.ws_manager import ws_manager
from datetime import datetime
from modules.chat_rag_langgraph import answer_question, generate_greeting
from modules.chat_summary import summarize_chat

from chatbot.chat_constants import DIVIDER_AI_END, DIVIDER_AI_START, EMPTY_ANSWER_FALLBACK, ENDFLOW_AI_RESPONDING, ENDFLOW_NEEDS_ADMIN, ENDFLOW_SUMMARIZING, MTYPE_AI_ANSWER, MTYPE_FREE_TEXT, MTYPE_MENU_SELECT, MTYPE_SYSTEM_NOTICE, SENDER_AI, SENDER_SYSTEM, SENDER_USER
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


def start_ai_consult(sno: str) -> dict:
    """
    AI 상담을 시작합니다. (동기 함수 — 라우터가 스레드풀에서 실행하므로 이벤트 루프를 막지 않음)
    1. 'AI 상담' 선택 로그 및 시작 구분선 저장
    2. ENDFLOW=6, CMODE=1을 LLM 호출 "전에" 먼저 저장
       (LLM 호출(인사말 생성) 중에 사용자가 방을 나갔다 들어와도, CMODE가
       이미 AI로 바뀌어 있어야 프론트의 restoreFromSession이 옵션형으로
       잘못 복원하지 않는다 — CMODE 전환이 LLM 호출 뒤에 있으면, 그 사이에
       재진입 시 CMODE는 옛날 값(0)인데 ENDFLOW만 6인 어중간한 상태가 되어
       화면이 옵션형으로 잘못 복원되는 버그가 있었다)
    3. LLM 인사말 생성, 저장
    4. ENDFLOW 초기화
    """
    ensure_session_open(sno)
    create_chat_log(sno, SENDER_USER, MTYPE_MENU_SELECT, "AI 상담")
    create_chat_log(sno, SENDER_SYSTEM, MTYPE_SYSTEM_NOTICE, DIVIDER_AI_START)

    # 1. LLM 호출 전에 ENDFLOW=6, CMODE=1을 먼저 저장 (재진입 시 상태 불일치 방지)
    update_endflow(sno, ENDFLOW_AI_RESPONDING)
    set_cmode(sno, 1)

    try:
        # 2. LLM 인사말 생성 — 실패해도 상담은 시작되도록 고정 인사말로 대체 (원인은 서버 로그에)
        try:
            greeting = (generate_greeting() or "").strip()
        except Exception:
            print(f"⚠ AI 상담 인사말 생성 실패(sno={sno}), 고정 인사말 사용")
            traceback.print_exc()
            greeting = ""
        greeting = greeting or "안녕하세요! 무엇이 궁금하신가요?"
        greeting_log_no = create_chat_log(sno, SENDER_AI, MTYPE_AI_ANSWER, greeting)

        # 3. 정상적으로 끝난 후 ENDFLOW 초기화
        update_endflow(sno, None)

    except Exception:
        # 실패 시 무한 로딩 방지를 위해 ENDFLOW 초기화 (CMODE는 이미 AI로 전환된 채 유지)
        update_endflow(sno, None)
        raise

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
