"""
chatbot/service.py

CHAT_LOG를 Oracle에서 직접 조회/저장하고, CHAT_SESSION을 직접 UPDATE합니다
(Spring REST API를 거치지 않음 - survey/service.py, cctv/service.py와 동일한 패턴).
"""

from datetime import datetime

from core.database import get_connection
from modules.chat_summary import summarize_chat
from modules.chat_rag import answer_question, generate_greeting
from chatbot.ws_manager import ws_manager

from langchain_core.messages import HumanMessage, AIMessage


# ── CHAT_LOG.SENDER ─────────────────────────────────────────────
SENDER_USER = 0
SENDER_AI = 1
SENDER_SYSTEM = 2

# ── CHAT_LOG.MTYPE ──────────────────────────────────────────────
MTYPE_MENU_SELECT = 0
MTYPE_FREE_TEXT = 2
MTYPE_AI_ANSWER = 4
MTYPE_SYSTEM_NOTICE = 5

# ── CHAT_SESSION.ENDFLOW (FastAPI가 직접 갱신하는 값) ─────────────
ENDFLOW_SUMMARIZING = 5
ENDFLOW_NEEDS_ADMIN = 4

# ── 구분선 고정 문구 ─────────────────────────────────────────────
DIVIDER_AI_START = "여기부터 AI 상담입니다"
DIVIDER_AI_END = "여기까지가 AI 상담입니다"


# =====================================================================
# 요약 기능 (관리자연결 시 QA 등록 폼 초기값 생성)
# =====================================================================

def get_chat_conversation(sno: str) -> str:
    """특정 세션의 CHAT_LOG를 시간순으로 조회해서, 메시지 유형까지 표시한 텍스트로 합친다."""

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
            speaker = "사용자" if sender == 0 else ("AI" if sender == 1 else "상담봇")
            mtype_label = MTYPE_LABEL.get(mtype, "기타")
            lines.append(f"[{mtype_label}] {speaker}: {content}")

        return "\n".join(lines)

    finally:
        cursor.close()
        connection.close()


def summarize_and_save(sno: str) -> dict:
    """
    세션의 대화 전체를 요약하고, CHAT_SESSION.STITLE에 즉시 저장한다.
    title은 채팅목록 타이틀로 바로 쓰이니 저장하고, content/type은 QA 등록
    폼 초기값으로만 쓰이므로 저장하지 않고 응답으로만 돌려준다.
    """

    _set_endflow(sno, ENDFLOW_SUMMARIZING)  # 시작 전에 먼저 저장

    try:
        conversation = get_chat_conversation(sno)
        if not conversation:
            raise ValueError(f"세션 {sno}의 대화 로그가 없습니다.")

        result = summarize_chat(conversation)
        _save_title_and_clear_endflow(sno, result["title"])  # 성공 시 STITLE 저장 + ENDFLOW 해제

        return result

    except Exception:
        _set_endflow(sno, None)  # 실패해도 대기상태는 풀어줌 (무한 로딩 방지)
        raise


def _set_endflow(sno: str, endflow) -> None:
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
    PK(NO)는 Spring(JPA)이 쓰는 CHAT_LOG_SEQ 시퀀스를 그대로 재사용합니다.

    sender=SENDER_USER(사용자)일 때는 READAT도 같이 갱신합니다 — 본인이 직접
    보낸 메시지는 이미 읽은 상태나 마찬가지이기 때문입니다. AI/시스템 메시지는
    READAT을 건드리지 않아, 사용자가 실제로 열람하기 전까지 "안읽음" 상태가
    유지됩니다.
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
    """CHAT_SESSION.ENDFLOW를 직접 UPDATE합니다. endflow=None이면 NULL로 해제합니다."""
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


def get_session_owner(sno: str) -> tuple:
    """세션의 소유자(mno, gno)를 조회합니다. (WebSocket 알림 대상 식별용)"""
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
    이번 세션에서, 가장 최근 'AI상담 시작' 구분선(DIVIDER_AI_START) 이후의
    대화(자유질문/AI답변)만 조회해서 LangChain 메시지 리스트로 변환합니다.

    같은 세션 안에서 "다른 질문하기"로 옵션형에 갔다가 다시 AI상담을 시작해도,
    start_ai_consult()가 구분선을 새로 INSERT하므로, 그 이전 구간의 대화는
    자동으로 맥락(History)에서 제외됩니다 — 별도 세션 분리 없이 구분선만으로
    경계를 관리합니다. 개수 제한(최근 N개 등)은 두지 않습니다(구간 자체가
    짧게 끊기는 구조라 불필요하다고 판단).
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
    AI 상담 시작.
    1. "AI 상담" 버튼 클릭 자체를 사용자 발화로 저장(응답에는 미포함 — 프론트가 즉시 그림)
    2. 구분선(고정 문구) 저장(응답에는 미포함 — 프론트가 즉시 그림)
    3. LLM이 생성한 인사말 저장
    4. CHAT_SESSION.CMODE를 1(AI상담)로 전환, ENDFLOW 잔재 초기화
    """
    create_chat_log(sno, SENDER_USER, MTYPE_MENU_SELECT, "AI 상담")
    create_chat_log(sno, SENDER_SYSTEM, MTYPE_SYSTEM_NOTICE, DIVIDER_AI_START)

    greeting = generate_greeting()
    greeting_log_no = create_chat_log(sno, SENDER_AI, MTYPE_AI_ANSWER, greeting)

    _set_cmode(sno, 1)
    update_endflow(sno, None)  # 이전 세션에 남아있던 ENDFLOW 잔재(예: 4) 초기화

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "logs": [
            {"no": greeting_log_no, "sender": SENDER_AI, "mtype": MTYPE_AI_ANSWER, "content": greeting, "cno": None, "cdate": now},
        ],
    }


def end_ai_consult_divider(sno: str) -> dict:
    """AI상담 중 다른질문하기/관리자문의/상담종료 클릭 시, 구분선(고정 문구)만 저장합니다."""
    log_no = create_chat_log(sno, SENDER_SYSTEM, MTYPE_SYSTEM_NOTICE, DIVIDER_AI_END)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "logs": [
            {"no": log_no, "sender": SENDER_SYSTEM, "mtype": MTYPE_SYSTEM_NOTICE, "content": DIVIDER_AI_END, "cno": None, "cdate": now},
        ],
    }


# =====================================================================
# AI 자유상담 (RAG 질의응답, 멀티턴 맥락 유지)
# =====================================================================
ENDFLOW_AI_RESPONDING = 6

async def process_ai_chat(sno: str, message: str) -> dict:
    history = get_ai_chat_history(sno)

    create_chat_log(sno, SENDER_USER, MTYPE_FREE_TEXT, message)
    update_endflow(sno, ENDFLOW_AI_RESPONDING)  # LLM 호출 시작 전에 먼저 저장

    try:
        result = answer_question(message, history)
        answer = result["answer"]
        needs_admin = result["needsAdmin"]

        ai_log_no = create_chat_log(sno, SENDER_AI, MTYPE_AI_ANSWER, answer)

        update_endflow(sno, ENDFLOW_NEEDS_ADMIN if needs_admin else None)

    except Exception:
        update_endflow(sno, None)  # 실패해도 대기상태는 풀어줌 (무한 로딩 방지)
        raise

    mno, gno = get_session_owner(sno)
    await ws_manager.notify(mno, gno, {"type": "new_message", "sno": sno})

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "logs": [{"no": ai_log_no, "sender": SENDER_AI, "mtype": MTYPE_AI_ANSWER, "content": answer, "cno": None, "cdate": now}],
        "needsAdmin": needs_admin,
    }