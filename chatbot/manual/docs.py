"""
첨부 매뉴얼(ATTACH_MANUAL) 등록/수정/삭제/목록. 업로드 때는 파일 저장만, 벡터화는 옵션생성 때.
"""

import os

from core.database import get_connection
from datetime import datetime

from chatbot.manual.config import UPLOAD_DIR
from chatbot.manual.db import _cleanup_empty_categories, _delete_ai_menus_of_doc, _has_legacy_menus
from chatbot.manual.vectors import _add_doc_to_vectorstore, _is_vectorized, _remove_doc_from_vectorstore


def _find_doc_no_by_filename(filename: str) -> int | None:
    """같은 파일명을 가진 기존 문서가 있으면 그 NO를 반환합니다(없으면 None)."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT NO FROM ATTACH_MANUAL WHERE FILENAME = :filename", {"filename": filename})
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        connection.close()


def upload_manual_doc(filename: str, file_bytes: bytes) -> dict:
    """
    md 문서를 서버 로컬에 저장하고 ATTACH_MANUAL에 기록합니다.
    벡터화는 하지 않습니다 — [AI 옵션생성] 첫 단계에서 수행됩니다.
    새로 등록된 문서는 UPDATEYN='N'(아직 AI생성에 반영 안 됨)으로 시작합니다.

    같은 파일명을 가진 문서가 이미 있으면, 새로 등록하는 대신 그 문서를
    덮어씁니다(update_manual_doc과 동일한 동작 — 같은 NO 유지, 파일 교체,
    벡터DB의 옛 내용 청크 삭제) — 관리자가 실수로 같은 파일을 다시
    업로드해도 중복 문서/중복 벡터가 쌓이지 않게 하기 위함입니다.
    """
    existing_no = _find_doc_no_by_filename(filename)
    if existing_no is not None:
        return update_manual_doc(existing_no, filename, file_bytes)

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 같은 파일명이 여러 번 업로드돼도 안 겹치게, 저장 파일명 앞에 타임스탬프를 붙인다
    safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
    upload_path = os.path.join(UPLOAD_DIR, safe_name)

    with open(upload_path, "wb") as f:
        f.write(file_bytes)

    connection = get_connection()
    cursor = connection.cursor()
    try:
        doc_no_var = cursor.var(int)
        cursor.execute(
            """
            INSERT INTO ATTACH_MANUAL (NO, FILENAME, UPLOAD_PATH, CDATE, UPDATEYN)
            VALUES (ATTACH_MANUAL_SEQ.NEXTVAL, :filename, :upload_path, :cdate, 'N')
            RETURNING NO INTO :doc_no
            """,
            {
                "filename": filename,
                "upload_path": upload_path,
                "cdate": now,
                "doc_no": doc_no_var,
            },
        )
        connection.commit()
        doc_no = int(doc_no_var.getvalue()[0])

    except Exception:
        connection.rollback()
        # DB 기록이 실패했으면 저장해둔 파일도 같이 정리
        if os.path.exists(upload_path):
            os.remove(upload_path)
        raise

    finally:
        cursor.close()
        connection.close()

    # 벡터화는 [AI 옵션생성] 때 수행 (업로드 속도 확보)
    return {"no": doc_no, "filename": filename, "cdate": now, "vectorized": False}


def update_manual_doc(doc_no: int, filename: str, file_bytes: bytes) -> dict:
    """
    이미 업로드된 문서를 새 파일로 교체합니다(같은 NO 유지). 기존 파일은
    삭제하고 새 파일을 저장하며, UPDATEYN을 'N'으로 리셋해 [AI 옵션생성]
    버튼이 다시 활성화되게 합니다. ChromaDB에서는 옛 내용의 청크만 지우고,
    새 내용 벡터화는 [AI 옵션생성] 때 합니다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT UPLOAD_PATH FROM ATTACH_MANUAL WHERE NO = :no", {"no": doc_no})
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"문서 {doc_no}를 찾을 수 없습니다.")
        old_upload_path = row[0]

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
        new_upload_path = os.path.join(UPLOAD_DIR, safe_name)

        with open(new_upload_path, "wb") as f:
            f.write(file_bytes)

        cursor.execute(
            """
            UPDATE ATTACH_MANUAL
            SET FILENAME = :filename, UPLOAD_PATH = :upload_path, CDATE = :cdate, UPDATEYN = 'N'
            WHERE NO = :no
            """,
            {"filename": filename, "upload_path": new_upload_path, "cdate": now, "no": doc_no},
        )
        connection.commit()

        # 교체가 끝난 뒤 기존 파일 정리
        if old_upload_path and os.path.exists(old_upload_path):
            os.remove(old_upload_path)

    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    # 옛 내용의 청크만 지워둔다(임베딩 없이 삭제만 하므로 빠름).
    # 옛 내용이 AI자유상담에 계속 검색되면 안 되기 때문. 새 내용 벡터화는 [AI 옵션생성] 때 수행.
    try:
        _remove_doc_from_vectorstore(doc_no)
    except Exception as e:
        print(f"⚠ 기존 벡터 삭제 실패 (문서 자체는 정상 교체됨): {e}")

    return {"no": doc_no, "filename": filename, "cdate": now, "vectorized": False}


def get_manual_docs() -> list[dict]:
    """현재 업로드되어 있는 문서 전체 목록을 조회합니다."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT NO, FILENAME, UPLOAD_PATH, CDATE, UPDATEYN
            FROM ATTACH_MANUAL
            ORDER BY NO
            """
        )
        rows = cursor.fetchall()
        return [
            {
                "no": no,
                "filename": filename,
                "uploadPath": upload_path,
                "cdate": cdate,
                "updateYn": update_yn,
            }
            for no, filename, upload_path, cdate, update_yn in rows
        ]
    finally:
        cursor.close()
        connection.close()


def is_generate_available() -> bool:
    """
    [AI 옵션생성] 버튼을 활성화해도 되는지 확인합니다.
    업로드된 문서가 하나 이상이고, 그중 UPDATEYN='N'(마지막 생성 이후
    등록/수정/삭제로 아직 반영 안 된 것)인 문서가 하나라도 있으면 True.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM ATTACH_MANUAL WHERE UPDATEYN = 'N'")
        if cursor.fetchone()[0] > 0:
            return True
        # AI가 최상위 메뉴를 만들던 예전 방식의 메뉴가 남아있으면 관리자 메뉴 아래로 재생성 필요
        legacy = _has_legacy_menus(cursor)
        if not legacy:
            return False
        cursor.execute("SELECT NO FROM ATTACH_MANUAL")
        return bool(legacy & {row[0] for row in cursor.fetchall()})
    finally:
        cursor.close()
        connection.close()


def delete_manual_doc(doc_no: int) -> None:
    """
    업로드된 문서를 삭제합니다 (파일 + DB 레코드 + ChromaDB 청크 + 이 문서로 만든 AI 메뉴).
    다른 문서들은 내용이 바뀐 게 아니므로 UPDATEYN을 건드리지 않습니다
    (문서별로 메뉴를 추적하므로 남은 문서를 다시 생성할 필요가 없음).
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT UPLOAD_PATH FROM ATTACH_MANUAL WHERE NO = :no", {"no": doc_no})
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"문서 {doc_no}를 찾을 수 없습니다.")
        upload_path = row[0]

        _delete_ai_menus_of_doc(cursor, doc_no)
        _cleanup_empty_categories(cursor)
        cursor.execute("DELETE FROM ATTACH_MANUAL WHERE NO = :no", {"no": doc_no})
        connection.commit()

        if upload_path and os.path.exists(upload_path):
            os.remove(upload_path)

    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    # DB 삭제가 끝난 뒤 벡터DB에서도 이 문서의 청크를 지움
    _remove_doc_from_vectorstore(doc_no)


def delete_manual_docs(doc_nos: list[int]) -> dict:
    """
    여러 문서를 한 번에 삭제합니다(첨부파일 일괄삭제).
    하나가 실패해도 나머지는 계속 지우고, 실패 목록을 돌려줍니다.
    """
    deleted, failed = [], []
    for doc_no in doc_nos:
        try:
            delete_manual_doc(doc_no)
            deleted.append(doc_no)
        except Exception as e:
            failed.append({"no": doc_no, "error": str(e)})
    return {"deleted": deleted, "failed": failed}


def retry_vectorize_docs() -> dict:
    """
    업로드된 문서 중, 아직 ChromaDB에 벡터화되지 않은 것만 다시 시도합니다.
    CHAT_MENU 생성은 건드리지 않고, 벡터화만 수행합니다.

    반환값: { "vectorizeFailedDocs": [...] } — 재시도 후에도 끝내 실패한 문서 목록
    """
    docs = get_manual_docs()
    vectorize_failed_docs = []

    for doc in docs:
        if _is_vectorized(doc["no"]):
            continue
        try:
            _add_doc_to_vectorstore(doc["uploadPath"], doc["no"])
        except Exception:
            vectorize_failed_docs.append({"no": doc["no"], "filename": doc["filename"]})

    return {"vectorizeFailedDocs": vectorize_failed_docs}
