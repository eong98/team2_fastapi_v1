"""
CHAT_MENU 저장·삭제·공개 — 문서(ANO)별 AI 메뉴 교체, 삭제 전 세션/로그 참조 해제.
"""

from core.database import get_connection

from chatbot.manual.config import AI_YN_AI_MANAGED, USEYN_HIDDEN, USEYN_VISIBLE
from chatbot.manual.rules import _fit_bytes


def _ensure_ano_column(cursor) -> None:
    cursor.execute(
        "SELECT COUNT(*) FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CHAT_MENU' AND COLUMN_NAME = 'ANO'"
    )
    if cursor.fetchone()[0] == 0:
        raise RuntimeError(
            "CHAT_MENU.ANO 컬럼이 없습니다. menu.sql의 ALTER TABLE CHAT_MENU ADD (ANO ...) 문을 먼저 실행해주세요."
        )


def _insert_menu(cursor, *, pno, step, label, answer, vseq, ano, now) -> int:
    no_var = cursor.var(int)
    cursor.execute(
        """
        INSERT INTO CHAT_MENU (NO, PNO, STEP, LABEL, ANSWER, VSEQ, USEYN, AIYN, ANO, CDATE)
        VALUES (CHAT_MENU_SEQ.NEXTVAL, :pno, :step, :label, :answer, :vseq, :useyn, :ai_yn, :ano, :cdate)
        RETURNING NO INTO :new_no
        """,
        {
            "pno": pno,
            "step": step,
            "label": _fit_bytes(label),
            "answer": answer,
            "vseq": vseq,
            "useyn": USEYN_HIDDEN,
            "ai_yn": AI_YN_AI_MANAGED,
            "ano": ano,
            "cdate": now,
            "new_no": no_var,
        },
    )
    return int(no_var.getvalue()[0])


def _detach_menu_refs(cursor, nos: list[int]) -> None:
    """
    CHAT_SESSION.CNO, CHAT_LOG.CNO가 CHAT_MENU.NO를 FK로 참조하므로, 메뉴를 지우기 전에
    그 메뉴를 가리키는 세션/로그의 CNO를 NULL로 바꾼다(대화 내용은 그대로 보존).
    이걸 안 하면 사용자가 한 번이라도 클릭한 메뉴는 삭제 시 ORA-02292로 실패한다.
    """
    if not nos:
        return
    rows = [(n,) for n in nos]
    cursor.executemany("UPDATE CHAT_SESSION SET CNO = NULL WHERE CNO = :1", rows)
    cursor.executemany("UPDATE CHAT_LOG SET CNO = NULL WHERE CNO = :1", rows)


def _delete_ai_menus_of_doc(cursor, doc_no: int) -> None:
    """
    이 문서(ANO=doc_no)로 AI가 만든 메뉴(AIYN='Y')를 하위 트리까지 통째로 삭제합니다.
    다른 문서로 만든 메뉴, ANO가 없는 메뉴는 건드리지 않습니다.
    PNO가 자기참조 FK라서 STEP이 큰(하위) 노드부터 지웁니다.
    """
    cursor.execute(
        """
        SELECT DISTINCT NO, STEP FROM CHAT_MENU
        START WITH AIYN = :ai_yn AND ANO = :ano
        CONNECT BY PRIOR NO = PNO
        """,
        {"ai_yn": AI_YN_AI_MANAGED, "ano": doc_no},
    )
    rows = cursor.fetchall()
    if not rows:
        return
    rows.sort(key=lambda r: r[1], reverse=True)
    _detach_menu_refs(cursor, [no for no, _ in rows])
    cursor.executemany("DELETE FROM CHAT_MENU WHERE NO = :1", [(no,) for no, _ in rows])


def _cleanup_empty_categories(cursor) -> None:
    """하위가 하나도 없는 AI 생성 최상위 메뉴(예전 방식의 공용 카테고리, AIYN='Y', ANO NULL)를 지웁니다."""
    cursor.execute(
        """
        SELECT c.NO FROM CHAT_MENU c
        WHERE c.PNO IS NULL AND c.AIYN = :ai_yn AND c.ANO IS NULL
          AND NOT EXISTS (SELECT 1 FROM CHAT_MENU ch WHERE ch.PNO = c.NO)
        """,
        {"ai_yn": AI_YN_AI_MANAGED},
    )
    empty = [row[0] for row in cursor.fetchall()]
    _detach_menu_refs(cursor, empty)
    cursor.executemany("DELETE FROM CHAT_MENU WHERE NO = :1", [(n,) for n in empty])


def _has_legacy_menus(cursor) -> set[int]:
    """
    AI가 최상위 메뉴를 만들던 예전 방식(문서별 STEP1, AI 공용 카테고리)으로 생성된 문서 번호들.
    지금은 최상위 메뉴를 관리자가 지정하므로, 이 문서들은 한 번 다시 생성해서 관리자 메뉴 아래로 옮긴다.
    """
    cursor.execute(
        """
        SELECT DISTINCT ANO FROM (
            SELECT t.ANO FROM CHAT_MENU t
            WHERE t.PNO IS NULL AND t.AIYN = :ai_yn AND t.ANO IS NOT NULL
            UNION
            SELECT c.ANO FROM CHAT_MENU c JOIN CHAT_MENU t ON c.PNO = t.NO
            WHERE t.PNO IS NULL AND t.AIYN = :ai_yn AND c.ANO IS NOT NULL
        )
        """,
        {"ai_yn": AI_YN_AI_MANAGED},
    )
    return {row[0] for row in cursor.fetchall()}


def publish_menu_nodes(top_no: int, nos: list[int]) -> None:
    """
    공용 카테고리(top_no)와, 이번에 생성된 하위메뉴(nos = STEP2 번호들)와 그 하위만 공개합니다.
    같은 카테고리 안의 다른 문서 메뉴나 관리자가 일부러 비공개로 둔 메뉴는 건드리지 않습니다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("UPDATE CHAT_MENU SET USEYN = :visible WHERE NO = :no", {"visible": USEYN_VISIBLE, "no": top_no})
        for no in nos:
            cursor.execute(
                """
                UPDATE CHAT_MENU SET USEYN = :visible
                WHERE NO IN (SELECT NO FROM CHAT_MENU START WITH NO = :no CONNECT BY PRIOR NO = PNO)
                """,
                {"visible": USEYN_VISIBLE, "no": no},
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def publish_menu_tree(top_no: int) -> None:
    """
    최상위 메뉴(top_no)와 그 하위(STEP2, STEP3) 전체의 USEYN을 'Y'(공개)로
    전환합니다. CONNECT BY로 하위 전체를 한 번에 찾아서 UPDATE합니다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE CHAT_MENU
            SET USEYN = :visible
            WHERE NO IN (
                SELECT NO FROM CHAT_MENU
                START WITH NO = :top_no
                CONNECT BY PRIOR NO = PNO
            )
            """,
            {"visible": USEYN_VISIBLE, "top_no": top_no},
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
