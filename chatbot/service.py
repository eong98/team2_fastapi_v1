"""
chatbot/service.py

CHAT_LOG를 Oracle에서 직접 조회/저장하고, CHAT_SESSION을 직접 UPDATE합니다
(Spring REST API를 거치지 않음 - survey/service.py, cctv/service.py와 동일한 패턴).
"""

import asyncio
from datetime import datetime
from langchain_core.messages import AIMessage, HumanMessage

from chatbot.ws_manager import ws_manager
from core.database import get_connection
from modules.chat_rag_langgraph import answer_question, generate_greeting
from modules.chat_summary import summarize_chat

# =====================================================================
# Constants (상수 정의)
# =====================================================================

# ── CHAT_LOG.SENDER ─────────────────────────────────────────────
SENDER_USER = 0
SENDER_AI = 1
SENDER_SYSTEM = 2

# ── CHAT_LOG.MTYPE ──────────────────────────────────────────────
MTYPE_MENU_SELECT = 0
MTYPE_FREE_TEXT = 2
MTYPE_AI_ANSWER = 4
MTYPE_SYSTEM_NOTICE = 5

# ── CHAT_SESSION.ENDFLOW (FastAPI가 직접 갱신하는 상태 값) ─────────
ENDFLOW_NEEDS_ADMIN = 4
ENDFLOW_SUMMARIZING = 5
ENDFLOW_AI_RESPONDING = 6

# ── 구분선 고정 문구 ─────────────────────────────────────────────
DIVIDER_AI_START = "여기부터 AI 상담입니다"
DIVIDER_AI_END = "여기까지가 AI 상담입니다"


# =====================================================================
# 요약 기능 (관리자연결 시 QA 등록 폼 초기값 생성)
# =====================================================================

def get_chat_conversation(sno: str) -> str:
    """특정 세션의 CHAT_LOG를 시간순으로 조회해서, 메시지 유형까지 표시한 텍스트로 합칩니다."""
    connection = get_connection()
    cursor = connection.cursor()

    MTYPE_LABEL = {
        0: "옵션선택",
        1: "옵션답변",
        2: "자유질문",
        3: "뒤로가기",
        4: "AI답변",
        5: "시스템안내",
    }

    try:
        cursor.execute(
            """
            SELECT SENDER, MTYPE, CONTENT
            FROM CHAT_LOG
            WHERE SNO = :sno
            ORDER BY NO
            """,
            {"sno": sno},
        )

        rows = cursor.fetchall()
        lines = []

        for sender, mtype, content in rows:
            if hasattr(content, "read"):
                content = content.read()
            speaker = "사용자" if sender == SENDER_USER else ("AI" if sender == SENDER_AI else "상담봇")
            mtype_label = MTYPE_LABEL.get(mtype, "기타")
            lines.append(f"[{mtype_label}] {speaker}: {content}")

        return "\n".join(lines)

    finally:
        cursor.close()
        connection.close()


def summarize_and_save(sno: str) -> dict:
    """
    세션의 대화 전체를 요약하고, CHAT_SESSION.STITLE에 즉시 저장합니다.
    title은 채팅목록 타이틀로 저장하며, content/type은 응답으로만 반환합니다.
    """
    _set_endflow(sno, ENDFLOW_SUMMARIZING)  # 시작 전에 상태 업데이트

    try:
        conversation = get_chat_conversation(sno)
        if not conversation:
            raise ValueError(f"세션 {sno}의 대화 로그가 없습니다.")

        result = summarize_chat(conversation)
        _save_title_and_clear_endflow(sno, result["title"])  # 성공 시 STITLE 저장 및 ENDFLOW 해제

        return result

    except Exception:
        _set_endflow(sno, None)  # 실패 시 대기상태 해제 (무한 로딩 방지)
        raise


def _set_endflow(sno: str, endflow) -> None:
    """CHAT_SESSION.ENDFLOW 전용 내부 갱신 함수"""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE CHAT_SESSION SET ENDFLOW = :endflow WHERE NO = :sno",
            {"endflow": endflow, "sno": sno},
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def _save_title_and_clear_endflow(sno: str, title: str) -> None:
    """STITLE 저장 및 ENDFLOW 초기화 내부 처리 함수"""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE CHAT_SESSION SET STITLE = :title, ENDFLOW = NULL WHERE NO = :sno",
            {"title": title, "sno": sno},
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


# =====================================================================
# CHAT_LOG 저장 / CHAT_SESSION 상태 갱신 공통 헬퍼
# =====================================================================

def create_chat_log(sno: str, sender: int, mtype: int, content: str, cno: int = None) -> int:
    """
    CHAT_LOG에 로그 한 건을 직접 INSERT하고, CHAT_SESSION.UDATE를 함께 갱신합니다.
    sender가 사용자일 경우 READAT도 동일하게 갱신 처리합니다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        log_no_var = cursor.var(int)
        cursor.execute(
            """
            INSERT INTO CHAT_LOG (NO, SNO, SENDER, MTYPE, CONTENT, CNO, CDATE)
            VALUES (CHAT_LOG_SEQ.NEXTVAL, :sno, :sender, :mtype, :content, :cno, :cdate)
            RETURNING NO INTO :log_no
            """,
            {
                "sno": sno,
                "sender": sender,
                "mtype": mtype,
                "content": content,
                "cno": cno,
                "cdate": now,
                "log_no": log_no_var,
            },
        )

        if sender == SENDER_USER:
            cursor.execute(
                "UPDATE CHAT_SESSION SET UDATE = :now, READAT = :now WHERE NO = :sno",
                {"now": now, "sno": sno},
            )
        else:
            cursor.execute(
                "UPDATE CHAT_SESSION SET UDATE = :now WHERE NO = :sno",
                {"now": now, "sno": sno},
            )

        connection.commit()
        return int(log_no_var.getvalue()[0])

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


def update_endflow(sno: str, endflow) -> None:
    """CHAT_SESSION.ENDFLOW를 직접 UPDATE합니다. (endflow=None 시 NULL)"""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE CHAT_SESSION SET ENDFLOW = :endflow WHERE NO = :sno",
            {"endflow": endflow, "sno": sno},
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _set_cmode(sno: str, cmode: int) -> None:
    """CHAT_SESSION.CMODE를 변경합니다."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE CHAT_SESSION SET CMODE = :cmode WHERE NO = :sno",
            {"cmode": cmode, "sno": sno},
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


CMODE_CLOSED = 2
EMPTY_ANSWER_FALLBACK = "죄송합니다. 지금은 답변을 만들지 못했습니다. 잠시 후 다시 질문해주시거나 관리자에게 문의해주세요."


def ensure_session_open(sno: str) -> None:
    """존재하지 않거나 이미 종료된(CMODE=2) 세션이면 ValueError — 라우터에서 400으로 응답."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT CMODE FROM CHAT_SESSION WHERE NO = :sno", {"sno": sno})
        row = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
    if row is None:
        raise ValueError("존재하지 않는 상담입니다.")
    if row[0] == CMODE_CLOSED:
        raise ValueError("이미 종료된 상담입니다.")


def get_session_owner(sno: str) -> tuple:
    """세션 소유자(mno, gno)를 조회합니다. (WebSocket 알림 식별용)"""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT MNO, GNO FROM CHAT_SESSION WHERE NO = :sno", {"sno": sno})
        row = cursor.fetchone()
        return (row[0], row[1]) if row else (None, None)
    finally:
        cursor.close()
        connection.close()


def get_ai_chat_history(sno: str) -> list:
    """
    최근 'AI상담 시작' 구분선(DIVIDER_AI_START) 이후의 대화 로그만 조회해
    LangChain 메시지 리스트로 변환합니다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT MAX(NO) FROM CHAT_LOG
            WHERE SNO = :sno AND DBMS_LOB.COMPARE(CONTENT, :divider) = 0
            """,
            {"sno": sno, "divider": DIVIDER_AI_START},
        )
        row = cursor.fetchone()
        start_no = row[0] if row and row[0] is not None else 0

        cursor.execute(
            """
            SELECT SENDER, CONTENT
            FROM CHAT_LOG
            WHERE SNO = :sno AND NO > :start_no AND MTYPE IN (:free_text, :ai_answer)
            ORDER BY NO
            """,
            {
                "sno": sno,
                "start_no": start_no,
                "free_text": MTYPE_FREE_TEXT,
                "ai_answer": MTYPE_AI_ANSWER,
            },
        )
        rows = cursor.fetchall()

        history = []
        for sender, content in rows:
            if hasattr(content, "read"):
                content = content.read()
            if sender == SENDER_USER:
                history.append(HumanMessage(content=content))
            elif sender == SENDER_AI:
                history.append(AIMessage(content=content))
        return history

    finally:
        cursor.close()
        connection.close()


# =====================================================================
# AI 상담 시작 / 종료 구분선
# =====================================================================

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
    _set_cmode(sno, 1)

    try:
        # 2. LLM 인사말 생성
        greeting = (generate_greeting() or "").strip() or "안녕하세요! 무엇이 궁금하신가요?"
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


# =====================================================================
# AI 자유상담 (RAG 질의응답)
# =====================================================================

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
        raise ValueError("메시지를 입력해주세요.")
    ensure_session_open(sno)

    history = get_ai_chat_history(sno)

    create_chat_log(sno, SENDER_USER, MTYPE_FREE_TEXT, message)
    update_endflow(sno, ENDFLOW_AI_RESPONDING)  # LLM 호출 시작 직전에 저장

    try:
        result = answer_question(message, history)
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