"""
chatbot/chat_repository.py

CHAT_LOG / CHAT_SESSION 조회·저장 (Oracle 직접, Spring을 거치지 않음).
"""

from core.database import get_connection
from datetime import datetime
from langchain_core.messages import AIMessage, HumanMessage

from chatbot.chat_constants import CMODE_CLOSED, DIVIDER_AI_START, MTYPE_AI_ANSWER, MTYPE_FREE_TEXT, SENDER_AI, SENDER_USER


class ChatRequestError(ValueError):
    """
    요청 자체가 잘못된 경우(없는/종료된 상담, 빈 메시지 등) — 라우터에서 400으로 응답.
    LLM·검색·DB 오류는 이 예외가 아니므로 500 + 서버 로그(traceback)로 구분된다.
    (LangChain의 JSON 해석 오류 OutputParserException도 ValueError라서, ValueError 전체를
     400으로 처리하면 AI 쪽 실패가 "잘못된 요청"으로 숨어버림)
    """


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


def set_cmode(sno: str, cmode: int) -> None:
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


def _fit_title(title: str, limit: int = 200) -> str:
    """STITLE은 VARCHAR2(200 BYTE) — 한글 기준 약 66자까지, 넘으면 바이트 기준으로 자름."""
    encoded = (title or "").strip().encode("utf-8")
    return encoded[:limit].decode("utf-8", errors="ignore").strip()


def save_title(sno: str, title: str) -> None:
    """채팅 목록 제목(STITLE)만 저장 — 진행 상태(ENDFLOW)는 건드리지 않음 (상담 종료 후 제목 요약용)."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE CHAT_SESSION SET STITLE = :title WHERE NO = :sno",
            {"title": _fit_title(title), "sno": sno},
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def save_title_and_clear_endflow(sno: str, title: str) -> None:
    """STITLE 저장 및 ENDFLOW 초기화 내부 처리 함수"""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE CHAT_SESSION SET STITLE = :title, ENDFLOW = NULL WHERE NO = :sno",
            {"title": _fit_title(title), "sno": sno},
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


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
        raise ChatRequestError("존재하지 않는 상담입니다.")
    if row[0] == CMODE_CLOSED:
        raise ChatRequestError("이미 종료된 상담입니다.")


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
