"""
chatbot/manual_doc.py

옵션형 메뉴 자동생성용 md 문서 관리 + AI 생성 로직.
ATTACH_MANUAL(순수 첨부파일 목록), CHAT_MENU(생성된 메뉴 트리)를 Oracle에
직접 저장합니다 (Spring REST API를 거치지 않음 — chatbot/service.py와 동일한 패턴).

흐름:
  1. upload_manual_doc(): md 파일을 서버 로컬(chatbot/data/manual_docs/)에
     저장하고 ATTACH_MANUAL에 기록만 합니다(업로드는 즉시 끝남).
     벡터화(ChromaDB, chatbot/data/chromadb_manual)는 업로드 때 하지 않고
     [AI 옵션생성] 첫 단계에서 "아직 벡터화 안 된 문서"만 처리합니다.
     → 새 매뉴얼은 옵션생성을 실행해야 AI자유상담(RAG) 검색에도 반영됩니다.
     각 청크에는 원본 문서 번호(attach_manual_no)를 메타데이터로 남겨서,
     문서를 교체/삭제할 때 그 문서에서 나온 청크만 정확히 지웁니다.
  2. start_generate_job(): UPDATEYN='N'(신규 등록/수정)인 문서만 대상으로
     백그라운드 스레드에서 생성하고, 진행 상황은 get_generate_job_status()로
     조회한다(새로고침해도 이어서 볼 수 있음). 문서별로
       ① 제목 구조(## 섹션 / ### 질문)가 있으면 목차를 그대로 트리로 쓰고
          원문 답변을 정리해서 사용 — LLM은 질문이 5개를 넘을 때 "고르기"만 함(빠름)
       ② 제목 구조가 없으면 벡터DB 청크로 LLM이 주제를 나누고, 주제마다
          유사도 검색(RAG)으로 관련 청크만 모아 하위 트리를 생성
       ③ 후처리(하위메뉴 최대 5개, 불필요한 하위메뉴 병합, URL 제거)
     후 CHAT_MENU에 USEYN='N'(비공개), ANO=문서번호로 INSERT.
     재생성 시에는 "그 문서(ANO)로 만든 AI 메뉴"만 지우고 다시 만든다 —
     다른 문서로 만든 기존 메뉴는 건드리지 않는다.
  3. publish_menu_tree(): 관리자가 미리보기를 검토(및 필요시 수정)한 뒤
     호출 — 해당 최상위 메뉴와 그 하위 전체의 USEYN을 'Y'(공개)로 전환.
"""

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from pydantic import BaseModel, Field


from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_chroma import Chroma

from core.database import get_connection
from core.llm_client import get_llm


# ── 파일 저장 경로 ──────────────────────────────────────────────
# chatbot/manual_doc.py 기준 상대경로로 고정 (실행 위치(cwd)와 무관하게 항상 같은 곳)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "manual_docs")

# AI자유상담(modules/ingest_manual.py, modules/manual_retriever.py)이 쓰는
# 것과 동일한 ChromaDB 경로. 여기에 합류시켜야 search_manual()이 같이 찾는다.
CHROMA_PERSIST_DIRECTORY = os.path.join(BASE_DIR, "data", "chromadb_manual")

# ── CHAT_MENU 상수 (chatbot/service.py와 동일 값 사용) ────────────
STEP_TOP = 1
STEP_MID = 2
STEP_LEAF = 3
USEYN_HIDDEN = "N"
USEYN_VISIBLE = "Y"
AI_YN_AI_MANAGED = "Y"   # AI가 생성한 노드 (재생성 시 삭제 대상)
AI_YN_MANUAL = "N"       # 관리자가 추가/수정한 노드 (재생성 시 항상 보존)


llm = get_llm()


def _get_chroma_embedding():
    """modules/ingest_manual.py의 get_embedding()과 동일한 설정(bge-m3)."""
    base_url = "http://localhost:11434"
    return OllamaEmbeddings(model="bge-m3", base_url=base_url)


def _add_doc_to_vectorstore(upload_path: str, doc_no: int) -> None:
    """
    업로드된 md 파일 하나를 청크로 쪼개서 기존 ChromaDB(AI자유상담용)에 추가합니다.
    modules/ingest_manual.py와 동일한 청크 분할 설정을 그대로 씁니다.

    각 청크의 메타데이터에 doc_no(ATTACH_MANUAL.NO)를 심어둡니다 — 나중에
    문서를 수정(파일교체)하거나 삭제할 때, 이 메타데이터로 "이 문서에서
    나온 청크만" 정확히 찾아서 지울 수 있게 하기 위함입니다.
    """
    loader = TextLoader(upload_path, encoding="utf-8")
    docs = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n## ", "\n\n", "\n", " ", ""],
    )
    splits = splitter.split_documents(docs)

    # 각 청크에 원본 문서 번호를 메타데이터로 부여 (삭제/재교체 시 필터링용)
    # chunk_idx는 옵션생성 시 청크를 원문 순서대로 다시 정렬하기 위한 값
    for idx, split in enumerate(splits):
        split.metadata["attach_manual_no"] = doc_no
        split.metadata["chunk_idx"] = idx

    embedding = _get_chroma_embedding()

    if not os.path.exists(CHROMA_PERSIST_DIRECTORY):
        Chroma.from_documents(
            documents=splits,
            embedding=embedding,
            persist_directory=CHROMA_PERSIST_DIRECTORY,
        )
    else:
        vectorstore = Chroma(
            persist_directory=CHROMA_PERSIST_DIRECTORY,
            embedding_function=embedding,
        )
        vectorstore.add_documents(splits)


def _remove_doc_from_vectorstore(doc_no: int) -> None:
    """
    특정 문서(doc_no)에서 나온 청크만 ChromaDB에서 찾아 삭제합니다.
    문서 수정(파일교체) 시 옛 내용을 지우거나, 문서 삭제 시 호출됩니다.
    """
    if not os.path.exists(CHROMA_PERSIST_DIRECTORY):
        return  # 벡터DB 자체가 없으면 지울 것도 없음

    embedding = _get_chroma_embedding()
    vectorstore = Chroma(
        persist_directory=CHROMA_PERSIST_DIRECTORY,
        embedding_function=embedding,
    )
    try:
        vectorstore.delete(where={"attach_manual_no": doc_no})
    except Exception as e:
        # 벡터화가 애초에 실패했던 문서라면 청크 자체가 없을 수 있음 — 조용히 무시
        print(f"⚠ 벡터DB 삭제 중 경고(문서 no={doc_no}): {e}")


def _is_vectorized(doc_no: int) -> bool:
    """이 문서(doc_no)에서 나온 청크가 ChromaDB에 실제로 존재하는지 확인합니다."""
    if not os.path.exists(CHROMA_PERSIST_DIRECTORY):
        return False

    embedding = _get_chroma_embedding()
    vectorstore = Chroma(
        persist_directory=CHROMA_PERSIST_DIRECTORY,
        embedding_function=embedding,
    )
    try:
        result = vectorstore.get(where={"attach_manual_no": doc_no})
        return len(result.get("ids", [])) > 0
    except Exception:
        return False


# =====================================================================
# 문서 업로드 / 조회 / 삭제 (순수 첨부파일 관리 + 벡터DB 등록)
# =====================================================================

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


class GenerateRunningError(RuntimeError):
    """AI 옵션생성 진행 중에는 매뉴얼 등록/교체/삭제를 막는다(생성 결과와 문서 상태가 어긋나지 않게)."""


def ensure_not_generating() -> None:
    with _job_lock:
        if _job.get("status") == "running":
            title = _JOB_RUNNERS[_job["kind"]][0]
            raise GenerateRunningError(f"{title}이 진행 중입니다. 끝난 뒤 다시 시도해주세요.")


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


# =====================================================================
# AI 옵션생성 (RAG 기반, 문서별·주제별 생성 → USEYN='N'으로 저장)
# =====================================================================

MAX_CHILDREN = 5          # 한 부모 아래 하위메뉴 최대 개수
MAX_TOPICS_PER_DOC = 6    # 문서 하나에서 만들 최상위(STEP1) 메뉴 최대 개수
TOPIC_CONTEXT_CHUNKS = 4  # 주제 하나 생성에 넘길 청크 최대 개수 (num_ctx 안에 들어가게)
# 주제별 LLM 병렬 호출 수. CPU 추론이면 동시에 돌려도 빨라지지 않으므로 GPU 서버에서만 늘릴 것
LLM_WORKERS = int(os.getenv("MENU_LLM_WORKERS", "3"))


def _build_menu_llm():
    """
    옵션생성 전용 LLM. 기본은 get_llm()과 같은 모델이며, 환경변수로 바꿀 수 있다.
      MENU_LLM_BASE_URL : 다른 Ollama 서버 사용 (예: SSH 터널로 연결한 H200 → http://localhost:11435)
      MENU_LLM_MODEL    : 다른 모델 사용 (예: 더 작은 모델로 CPU 속도 확보)
    num_predict로 출력 길이를 제한해 CPU에서 끝없이 길게 생성하는 것을 막고,
    keep_alive로 생성 도중 모델이 내려갔다 다시 올라오는 지연을 없앤다.
    """
    base = get_llm()
    return ChatOllama(
        model=os.getenv("MENU_LLM_MODEL") or base.model,
        base_url=os.getenv("MENU_LLM_BASE_URL") or base.base_url,
        temperature=0,
        num_ctx=4096,
        num_predict=1200,
        keep_alive="30m",
    )


menu_llm = _build_menu_llm()


class TopicItem(BaseModel):
    label: str = Field(description="최상위(STEP1) 메뉴로 쓸 주제명. 15자 이내의 짧은 명사형")
    query: str = Field(description="이 주제의 내용을 문서에서 검색할 때 쓸 검색 문장")
    chunks: list[int] = Field(default_factory=list, description="이 주제 내용이 들어있는 청크 번호 목록")


class TopicPlan(BaseModel):
    topics: list[TopicItem] = Field(description="문서에 들어있는 서로 다른 주제 목록")


class MenuLeaf(BaseModel):
    label: str = Field(description="STEP3 선택지에 표시될 짧은 질문형 텍스트")
    answer: str = Field(description="이 선택지를 클릭했을 때 보여줄 답변(문서 근거, URL/경로 금지)")


class MenuMid(BaseModel):
    label: str = Field(description="STEP2 선택지에 표시될 짧은 텍스트")
    answer: str | None = Field(
        default=None,
        description=(
            "leaves가 비어 있으면 반드시 문서 근거로 사용자가 이해하기 쉬운 답변을 작성하세요. "
            "leaves가 있으면 null로 두세요. URL이나 /user/qa 같은 경로는 절대 쓰지 마세요."
        ),
    )
    leaves: list[MenuLeaf] = Field(
        default_factory=list,
        description="STEP3 하위 선택지 (0개 또는 2~5개). 1개뿐이거나 STEP2와 같은 내용이면 만들지 마세요.",
    )


class MenuTop(BaseModel):
    label: str = Field(description="최상위(STEP1) 카테고리명")
    children: list[MenuMid] = Field(description="STEP2 하위 선택지 목록 (최대 5개)")


structured_topic_llm = menu_llm.with_structured_output(TopicPlan)
structured_top_llm = menu_llm.with_structured_output(MenuTop)


# ── 텍스트 정리 ──────────────────────────────────────────────────
_URL_RE = re.compile(r"https?://[^\s)\]>'\"]+|www\.[^\s)\]>'\"]+")
# /user/qa, /dbms/chat_menu 같은 내부 경로 (앞이 단어/숫자/슬래시면 제외 → 24/7, and/or 보호)
_PATH_RE = re.compile(r"(?<![\w/.:])/[A-Za-z_][\w\-]*(?:/[\w\-{}:.]+)*/?")
_EMPTY_WRAP_RE = re.compile(r"\(\s*[,:]?\s*\)|\[\s*\]|`\s*`|<\s*>")
_DANGLING_LABEL_RE = re.compile(r"(경로|주소|링크|URL|url|페이지 주소)\s*[:：]\s*(?=[\n,.)]|$)")


def _strip_urls(text: str | None) -> str:
    """답변/문맥에서 URL과 /user/qa 같은 내부 경로를 제거합니다."""
    if not text:
        return ""
    text = _URL_RE.sub("", text)
    text = _PATH_RE.sub("", text)
    text = _DANGLING_LABEL_RE.sub("", text)
    text = _EMPTY_WRAP_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.)])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _is_similar(a: str, b: str) -> bool:
    """
    두 라벨이 사실상 같은 주제인지. 짧은 쪽이 긴 쪽에 포함되더라도 길이 차이가 크면
    (예: '공지사항' vs '공지사항은 어떻게 보나요') 하위 질문이므로 같은 주제로 보지 않는다.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    if short in long_ and len(short) / len(long_) >= 0.6:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.75


def _top_guide(label: str) -> str:
    return f"'{label}'에 관한 질문을 선택해주세요"


# ── 트리 후처리 (5번, 6번, 8번, 9번 규칙을 코드로 한 번 더 보장) ──────
def _normalize_mid(mid: dict) -> dict | None:
    label = _fit_bytes(_strip_urls(mid.get("label")))
    answer = _strip_urls(mid.get("answer"))
    if not label:
        return None

    leaves = []
    for leaf in mid.get("leaves") or []:
        l_label = _fit_bytes(_strip_urls(leaf.get("label")))
        l_answer = _strip_urls(leaf.get("answer"))
        if not l_label or not l_answer:
            continue
        # 부모(STEP2)와 사실상 같은 주제인 하위메뉴는 만들지 않고 답변만 부모로 흡수
        if _is_similar(l_label, label):
            answer = answer or l_answer
            continue
        if any(_is_similar(l_label, x["label"]) for x in leaves):
            continue
        leaves.append({"label": l_label, "answer": l_answer})

    # 하위메뉴가 1개뿐이면 굳이 한 단계 더 들어갈 필요가 없으므로 부모로 합침
    if len(leaves) == 1:
        only = leaves[0]["answer"]
        answer = f"{answer}\n\n{only}" if answer and answer != _top_guide(label) else only
        leaves = []

    leaves = leaves[:MAX_CHILDREN]

    if leaves:
        answer = answer or _top_guide(label)
    elif not answer:
        return None  # 하위도 답변도 없는 빈 메뉴는 만들지 않음

    return {"label": label, "answer": answer, "leaves": leaves}


def _normalize_top(top: dict) -> dict | None:
    label = _strip_urls(top.get("label"))
    if not label:
        return None
    top_answer = _strip_urls(top.get("answer")) or None
    if top_answer == _top_guide(label):
        top_answer = None

    mids: list[dict] = []
    for raw in top.get("children") or []:
        mid = _normalize_mid(raw)
        if mid is None:
            continue
        if _is_similar(mid["label"], label):
            if mid["leaves"]:
                # 부모와 같은 주제의 중간메뉴 → 그 하위를 한 단계 끌어올림
                mids.extend({"label": l["label"], "answer": l["answer"], "leaves": []} for l in mid["leaves"])
            else:
                # 부모와 같은 주제의 개요성 답변 → 최상위 답변으로 사용
                top_answer = f"{top_answer}\n\n{mid['answer']}" if top_answer else mid["answer"]
            continue
        dup = next((m for m in mids if _is_similar(m["label"], mid["label"])), None)
        if dup is not None:
            continue
        mids.append(mid)

    if len(mids) == 1:
        only = mids[0]
        if only["leaves"]:
            mids = [{"label": l["label"], "answer": l["answer"], "leaves": []} for l in only["leaves"]]
        else:
            top_answer = f"{top_answer}\n\n{only['answer']}" if top_answer else only["answer"]
            mids = []

    mids = mids[:MAX_CHILDREN]

    if mids:
        top_answer = top_answer or _top_guide(label)
    elif not top_answer:
        return None

    return {"label": label, "answer": top_answer, "children": mids}


# ── 벡터DB 조회 ───────────────────────────────────────────────────
_vectorstore_cache = None


def _get_vectorstore():
    global _vectorstore_cache
    if _vectorstore_cache is None:
        _vectorstore_cache = Chroma(
            persist_directory=CHROMA_PERSIST_DIRECTORY,
            embedding_function=_get_chroma_embedding(),
        )
    return _vectorstore_cache


def _load_doc_chunks(doc: dict) -> list[str]:
    """
    벡터화된 이 문서의 청크를 원문 순서대로 가져옵니다.
    벡터DB에 없으면(벡터화 실패) 파일을 직접 같은 규칙으로 잘라서 씁니다.
    """
    try:
        res = _get_vectorstore().get(
            where={"attach_manual_no": doc["no"]}, include=["documents", "metadatas"]
        )
        pairs = list(zip(res.get("documents") or [], res.get("metadatas") or []))
        if pairs:
            pairs.sort(key=lambda p: (p[1] or {}).get("chunk_idx", 0))
            return [text for text, _ in pairs]
    except Exception as e:
        print(f"⚠ 벡터DB 청크 조회 실패(문서 no={doc['no']}): {e}")

    path = doc.get("uploadPath")
    if not path or not os.path.exists(path):
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800, chunk_overlap=100, separators=["\n## ", "\n\n", "\n", " ", ""]
    )
    return [d.page_content for d in splitter.split_documents(TextLoader(path, encoding="utf-8").load())]


def _search_doc_chunks(doc_no: int, query: str, k: int) -> list[str]:
    """이 문서 안에서만 주제와 유사한 청크를 검색합니다(RAG)."""
    try:
        hits = _get_vectorstore().similarity_search(query, k=k, filter={"attach_manual_no": doc_no})
        return [h.page_content for h in hits]
    except Exception as e:
        print(f"⚠ 유사도 검색 실패(문서 no={doc_no}): {e}")
        return []


def _chunk_digest(chunks: list[str]) -> str:
    """주제 계획용 요약본 — 청크마다 제목줄과 앞부분만 짧게 보여준다(입력 토큰 절약)."""
    lines = []
    for i, chunk in enumerate(chunks):
        heads = [ln.strip() for ln in chunk.splitlines() if ln.strip().startswith("#")]
        body = " ".join(ln.strip() for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("#"))
        lines.append(f"[{i}] {' / '.join(heads)[:150]} :: {_strip_urls(body)[:160]}")
    return "\n".join(lines)


# ── LLM 호출 ─────────────────────────────────────────────────────
def _plan_topics(doc: dict, chunks: list[str]) -> list[dict]:
    """문서 하나를 주제별로 나눕니다. 실패하면 문서 전체를 한 주제로 취급."""
    title = os.path.splitext(doc["filename"])[0]
    system_prompt = f"""
당신은 고객상담 챗봇 메뉴 설계자입니다. 아래는 매뉴얼 문서 '{doc['filename']}'를
청크 단위로 요약한 목록입니다. 이 문서에 들어있는 "서로 다른 주제"를 찾아 나누세요.

[규칙]
- 문서 하나에 여러 주제(예: 구독, 결제, 공지사항, Q&A 등)가 있으면 반드시 주제별로 분리하세요.
  문서 전체를 하나의 주제로 뭉치지 마세요. 단, 같은 주제를 억지로 쪼개지도 마세요.
- 주제는 최대 {MAX_TOPICS_PER_DOC}개. label은 15자 이내 명사형, 사용자가 메뉴에서 바로 알아볼 수 있게.
- chunks에는 그 주제 내용이 들어있는 청크 번호를 모두 적으세요.
- query는 그 주제 내용을 검색하기 좋은 한 문장으로 쓰세요.

청크 목록:
{_chunk_digest(chunks)}
"""
    try:
        plan = structured_topic_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content="주제 목록을 만들어주세요.")]
        )
        topics = []
        for t in plan.topics[:MAX_TOPICS_PER_DOC]:
            label = _strip_urls(t.label)
            if not label:
                continue
            ids = [i for i in t.chunks if isinstance(i, int) and 0 <= i < len(chunks)]
            topics.append({"label": label, "query": t.query or label, "chunks": ids})
        if topics:
            return topics
    except Exception as e:
        print(f"⚠ 주제 분석 실패, 문서 전체를 한 주제로 처리(문서 no={doc['no']}): {e}")
    return [{"label": title, "query": title, "chunks": list(range(len(chunks)))}]


def _topic_context(doc_no: int, chunks: list[str], topic: dict) -> str:
    """주제에 배정된 청크 + 벡터 유사도 검색 결과를 합쳐 문맥을 만듭니다."""
    picked: list[str] = [chunks[i] for i in topic["chunks"]]
    for text in _search_doc_chunks(doc_no, f"{topic['label']} {topic['query']}", k=4):
        if text not in picked:
            picked.append(text)
    if not picked:
        picked = chunks
    picked = picked[:TOPIC_CONTEXT_CHUNKS]
    # 원문 순서 유지
    order = {c: i for i, c in enumerate(chunks)}
    picked.sort(key=lambda c: order.get(c, 10**6))
    return "\n\n---\n\n".join(_strip_urls(c) for c in picked)


def _generate_topic_tree(topic: dict, context: str) -> dict | None:
    system_prompt = f"""
당신은 고객상담 챗봇의 옵션형 메뉴(선택지 트리)를 설계하는 AI입니다.
주제 '{topic['label']}'에 대해, 아래 문서 내용만 근거로 선택지 트리를 만드세요.
최상위(STEP1) label은 '{topic['label']}'로 하세요.

[생성 규칙]
1. STEP2(children)는 최대 {MAX_CHILDREN}개. 사용자가 가장 많이 궁금해할 핵심만 골라 간략하게 추리세요.
2. STEP2 하나로 바로 답할 수 있으면 answer에 답변을 쓰고 leaves는 비워두세요.
   세부 질문이 2개 이상 필요한 경우에만 leaves(STEP3, 최대 {MAX_CHILDREN}개)를 만들고 answer는 null로 두세요.
3. 불필요한 하위메뉴 금지:
   - 하위 선택지가 1개뿐이면 leaves를 만들지 말고 그 내용을 STEP2 answer에 바로 쓰세요.
   - 하위 선택지가 부모와 주제가 같거나 거의 비슷하면 만들지 마세요.
   - STEP2가 STEP1과 같은 주제의 반복이면 만들지 마세요.
4. leaves가 없는 선택지의 answer는 절대 null/빈값이면 안 됩니다. 반드시 문서 근거로 2~4문장 이내로 작성하세요.
5. answer에 URL, 링크, '/user/qa' 같은 페이지 경로를 절대 넣지 마세요.
   위치를 안내해야 하면 "고객센터 > Q&A 메뉴"처럼 화면 메뉴 이름으로만 설명하세요.
6. 문서에 없는 내용은 지어내지 마세요. label은 짧고 명확하게.

문서 내용:
{context}
"""
    result = structured_top_llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content="이 주제의 메뉴 트리를 만들어주세요.")]
    )
    raw = result.model_dump()
    raw["label"] = topic["label"]  # 최상위 이름은 주제 계획 단계에서 정한 이름으로 고정
    return _normalize_top(raw)


# ── DB 저장 ──────────────────────────────────────────────────────
def _ensure_ano_column(cursor) -> None:
    cursor.execute(
        "SELECT COUNT(*) FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CHAT_MENU' AND COLUMN_NAME = 'ANO'"
    )
    if cursor.fetchone()[0] == 0:
        raise RuntimeError(
            "CHAT_MENU.ANO 컬럼이 없습니다. menu.sql의 ALTER TABLE CHAT_MENU ADD (ANO ...) 문을 먼저 실행해주세요."
        )


def _fit_bytes(text: str, limit: int = 100) -> str:
    """LABEL은 VARCHAR2(100) BYTE라 한글(3바이트)은 33자 정도가 한계 — 바이트 기준으로 자른다."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[: limit - 3].decode("utf-8", errors="ignore").rstrip() + "…"


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


# ── 목차(마크다운 제목) 기반 빠른 생성 ─────────────────────────────
# 매뉴얼이 "## 섹션 → ### 질문 → 답변" 처럼 제목 구조를 갖고 있으면, 그 구조가
# 곧 메뉴 트리다. 이 경우 LLM에게 답변을 새로 쓰게 하지 않고 원문 답변을 그대로
# 정리해서 쓴다(CPU에서 가장 느린 "긴 답변 생성"을 통째로 생략 → 수 초 내 완료).
# LLM은 질문이 5개를 넘는 섹션에서 "중요한 5개 고르기"에만 짧게 쓴다.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LABEL_PREFIX_RE = re.compile(r"^\s*(?:Q\s*[.:)]\s*|\d+(?:\.\d+)*\s*[.)]\s*)", re.IGNORECASE)
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


class ItemPick(BaseModel):
    picks: list[int] = Field(description="사용자에게 가장 중요한 질문 번호 (최대 5개)")


structured_pick_llm = menu_llm.with_structured_output(ItemPick)


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _md_to_plain(text: str) -> str:
    """마크다운 본문을 챗봇 답변용 평문으로 정리합니다(표 → 목록, 강조/구분선/URL 제거)."""
    lines = (text or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _table_cells(line)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _table_cells(lines[i])
                parts = [f"{h}: {v}" for h, v in zip(header[1:], cells[1:]) if v and v != "-"]
                out.append(f"- {cells[0]}" + (f" ({' / '.join(parts)})" if parts else ""))
                i += 1
            continue
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            i += 1
            continue
        out.append(line)
        i += 1
    plain = "\n".join(out)
    plain = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), plain)
    plain = plain.replace("`", "")
    return _strip_urls(plain)


def _clean_label(title: str) -> str:
    label = _LABEL_PREFIX_RE.sub("", title or "")
    label = re.sub(r"\*\*|__|`", "", label)
    return _strip_urls(label)


def _parse_md_structure(text: str) -> list[dict] | None:
    """
    제목 구조를 분석해서 [{title, intro, items:[{title, body}]}] 로 돌려줍니다.
    2번 이상 나오는 가장 얕은 제목 단계를 "주제", 그 바로 아래 단계를 "항목"으로 봅니다.
    제목 구조가 없으면 None → RAG + LLM 생성으로 처리.
    """
    lines = (text or "").splitlines()
    heads: list[tuple[int, int, str]] = []
    in_code = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else _HEADING_RE.match(line)
        if m:
            heads.append((idx, len(m.group(1)), m.group(2).strip()))
    if not heads:
        return None

    counts: dict[int, int] = {}
    for _, lvl, _ in heads:
        counts[lvl] = counts.get(lvl, 0) + 1
    topic_level = next((lvl for lvl in sorted(counts) if counts[lvl] >= 2), None)
    if topic_level is None:
        return None

    def body(k: int) -> str:
        start = heads[k][0] + 1
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        return "\n".join(lines[start:end])

    topics: list[dict] = []
    for k, (_, lvl, title) in enumerate(heads):
        if lvl < topic_level:
            continue  # 문서 제목(# ...) 등
        if lvl == topic_level:
            topics.append({"title": title, "intro": body(k), "items": []})
        elif topics:
            if lvl == topic_level + 1:
                topics[-1]["items"].append({"title": title, "body": body(k)})
            else:  # 더 깊은 제목은 직전 항목(없으면 주제 소개)에 이어붙임
                extra = f"{title}\n{body(k)}"
                if topics[-1]["items"]:
                    topics[-1]["items"][-1]["body"] += "\n" + extra
                else:
                    topics[-1]["intro"] += "\n" + extra
    topics = [t for t in topics if t["items"] or _md_to_plain(t["intro"])]
    return topics or None


def _read_doc_structure(doc: dict) -> list[dict] | None:
    path = doc.get("uploadPath")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_md_structure(f.read())
    except Exception as e:
        print(f"⚠ 문서 구조 분석 실패(문서 no={doc['no']}): {e}")
        return None


def _pick_important(topic_label: str, items: list[dict], k: int = MAX_CHILDREN) -> list[dict]:
    """질문이 5개를 넘을 때만 호출 — LLM이 번호만 고르므로 출력이 매우 짧아 빠르다."""
    numbered = "\n".join(f"[{i}] {it['label']}" for i, it in enumerate(items))
    system_prompt = f"""
고객상담 챗봇의 '{topic_label}' 메뉴에 넣을 질문을 고릅니다.
아래 질문 중 사용자가 가장 많이 궁금해할 핵심 질문을 최대 {k}개 골라 번호만 답하세요.
서로 내용이 겹치는 질문은 하나만 고르세요.

{numbered}
"""
    try:
        res = structured_pick_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content="번호를 골라주세요.")]
        )
        picks = sorted({i for i in res.picks if isinstance(i, int) and 0 <= i < len(items)})[:k]
        if picks:
            return [items[i] for i in picks]
    except Exception as e:
        print(f"⚠ 중요 질문 선택 실패, 앞에서부터 {k}개 사용: {e}")
    return items[:k]


_VAGUE_LABELS = {
    "기타", "정보", "일반", "안내", "기타문의", "기타안내", "기타정보", "기타사항", "일반정보", "기본정보", "그외",
    "도움말", "도움", "기능", "기능안내", "주요기능", "서비스", "서비스안내", "이용안내", "사용안내", "기본", "기본기능", "사용법",
}


def _lob_str(value) -> str:
    """Oracle CLOB(ANSWER)은 LOB 객체로 오므로 문자열로 변환."""
    if value is None:
        return ""
    return value.read() if hasattr(value, "read") else str(value)


def _section_items(sec: dict) -> list[dict]:
    """목차 섹션의 질문들 → STEP3 후보. 5개를 넘으면 LLM이 중요한 것만 고른다."""
    label = _clean_label(sec["title"])
    items = [{"label": _clean_label(it["title"]), "answer": _md_to_plain(it["body"])} for it in sec["items"]]
    items = [it for it in items if it["label"] and it["answer"]]
    if len(items) > MAX_CHILDREN:
        items = _pick_important(label, items)
    return items


# ── 최상위 메뉴(STEP1) 분류 ─────────────────────────────────────
# 최상위 메뉴는 관리자가 직접 등록한다(AIYN='N', 최대 MAX_CATEGORIES개). AI는 후보 이름만 추천하고
# (suggest_categories), [AI 옵션생성] 때는 매뉴얼 섹션을 관리자 메뉴 중 하나로 분류만 한다.
#   STEP1 = 관리자 최상위 메뉴 (모든 매뉴얼 공용, 문서 소유 아님)
#   STEP2 = 매뉴얼의 섹션/주제 (AIYN='Y', ANO=문서번호)
#   STEP3 = 질문 (AIYN='Y', ANO=문서번호)
# 문서를 수정/삭제하면 그 문서의 STEP2·3만 교체/삭제된다.
MAX_CATEGORIES = 6


class SectionCategories(BaseModel):
    picks: list[int] = Field(description="섹션 순서대로, 각 섹션이 들어갈 카테고리 번호 (맞는 곳이 없으면 -1)")


structured_classify_llm = menu_llm.with_structured_output(SectionCategories)


def _load_existing_categories(cursor, target_nos: set[int]) -> list[dict]:
    """
    관리자가 직접 만든 최상위 메뉴(STEP1, AIYN='N')만 카테고리로 씁니다(최대 MAX_CATEGORIES개).
    used = 이번 생성 후에도 남는 하위 개수(대상 문서에서 나온 노드는 곧 지워지므로 제외).
    desc = 분류 참고용 설명(관리자가 적은 답변 + 이미 들어있는 하위메뉴 이름).
    """
    cursor.execute(
        "SELECT NO, LABEL, ANSWER FROM CHAT_MENU WHERE PNO IS NULL AND AIYN = :manual ORDER BY VSEQ, NO",
        {"manual": AI_YN_MANUAL},
    )
    tops = cursor.fetchall()
    cursor.execute(
        "SELECT PNO, ANO, LABEL FROM CHAT_MENU WHERE PNO IN (SELECT NO FROM CHAT_MENU WHERE PNO IS NULL)"
    )
    used: dict[int, int] = {}
    child_labels: dict[int, list[str]] = {}
    for pno, ano, label in cursor.fetchall():
        if ano is not None and ano in target_nos:
            continue
        used[pno] = used.get(pno, 0) + 1
        child_labels.setdefault(pno, []).append(label)

    result = []
    for no, label, answer in tops[:MAX_CATEGORIES]:
        answer = _lob_str(answer)
        desc_parts = []
        if answer and answer != _top_guide(label):
            desc_parts.append(_strip_urls(answer)[:150])
        if child_labels.get(no):
            desc_parts.append("포함: " + " / ".join(child_labels[no][:5]))
        result.append({"no": no, "label": label, "answer": answer, "desc": " · ".join(desc_parts), "used": used.get(no, 0)})
    return result


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _embedding_picks(cats: list[dict], sections: list[dict]) -> list[int]:
    """LLM 분류가 빠뜨린 섹션용 대체 — 임베딩(bge-m3) 유사도가 가장 높은 카테고리."""
    emb = _get_chroma_embedding()
    cat_vecs = emb.embed_documents([f"{c['label']}. {c['desc']}" for c in cats])
    sec_vecs = emb.embed_documents([f"{s['label']}. {s['hint']}" for s in sections])
    return [max(range(len(cats)), key=lambda j: _cosine(sv, cat_vecs[j])) for sv in sec_vecs]


def _classify_doc_sections(cats: list[dict], doc_sections: list[dict]) -> list[int]:
    """
    매뉴얼 하나의 섹션들을 관리자 카테고리 중 하나로 분류합니다.
    출력은 섹션 수만큼의 번호 목록이라 짧고, 매뉴얼 단위라 입력도 작아 로컬 CPU에서도 빠르다.
    번호가 비었거나 잘못되면 그 섹션만 임베딩 유사도로 채운다.
    """
    cat_lines = "\n".join(f"[{j}] {c['label']}" + (f" — {c['desc']}" if c["desc"] else "") for j, c in enumerate(cats))
    sec_lines = "\n".join(
        f"{i + 1}. {s['label']}" + (f" (질문: {s['hint']})" if s["hint"] else "") for i, s in enumerate(doc_sections)
    )
    system_prompt = f"""
고객상담 챗봇 메뉴를 정리합니다. 아래 매뉴얼 섹션 각각을 가장 알맞은 최상위 카테고리 하나에 분류하세요.

[카테고리]
{cat_lines}

[섹션 — 매뉴얼 '{doc_sections[0]['doc']['filename']}']
{sec_lines}

[규칙]
- picks에는 섹션 순서(1번부터 {len(doc_sections)}번까지)대로 카테고리 번호를 정확히 {len(doc_sections)}개 적으세요.
- 사용자가 그 섹션 내용을 찾으러 들어갈 카테고리를 고르세요.
- 어느 카테고리에도 맞지 않으면 -1을 적으세요.
"""
    picks: list[int] = []
    try:
        res = structured_classify_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content="섹션을 분류해주세요.")]
        )
        picks = list(res.picks)
    except Exception as e:
        print(f"⚠ 섹션 분류 실패, 임베딩으로 대체: {e}")

    picks = (picks + [None] * len(doc_sections))[: len(doc_sections)]
    missing = [i for i, p in enumerate(picks) if not isinstance(p, int) or not (-1 <= p < len(cats))]
    if missing:
        fallback = _embedding_picks(cats, [doc_sections[i] for i in missing])
        for i, j in zip(missing, fallback):
            picks[i] = j
    return picks


class CategoryName(BaseModel):
    label: str = Field(description="최상위 메뉴 이름. 10자 이내의 온전한 명사 (예: 고객센터, 문의사항, 매장 관리)")
    desc: str = Field(description="이 메뉴에 들어갈 내용 한 줄 설명 (사용자에게 보여줄 안내 문구로도 씀)")


class CategoryNames(BaseModel):
    categories: list[CategoryName] = Field(description="추천하는 최상위 메뉴 목록")


structured_catname_llm = menu_llm.with_structured_output(CategoryNames)


def suggest_categories(rep: "_Reporter | None" = None) -> dict:
    """
    [최상위 메뉴 AI 추천] — 누를 때마다 매뉴얼 전체 섹션을 보고 최상위 메뉴 후보를 처음부터 새로
    최대 MAX_CATEGORIES개 만듭니다(기존 메뉴 수와 무관). DB에는 저장하지 않습니다.
    관리자가 골라 [등록]하면 replace_top_menus()가 최상위 메뉴 전체를 그 목록으로 교체합니다.
    반환: { existing: [현재 관리자 최상위 메뉴 이름], max: 최대 개수,
            totalSections: 매뉴얼 섹션 수(메뉴당 최대 5개라 자리 계산용), suggestions: [{label, desc}] }
    """
    rep = rep or _Reporter(_new_job())
    rep.step("read", "등록된 최상위 메뉴와 매뉴얼 목차 읽는 중", 5)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        existing = _load_existing_categories(cursor, set())
    finally:
        cursor.close()
        connection.close()
    by_doc: list[str] = []
    total_sections = 0
    for doc in get_manual_docs():
        structure = _read_doc_structure(doc)
        if structure:
            total_sections += len(structure)
            by_doc.append(f"- {doc['filename']}: " + " / ".join(_clean_label(sec["title"]) for sec in structure))
    base = {"existing": [c["label"] for c in existing], "max": MAX_CATEGORIES, "totalSections": total_sections}
    rep.done("read", f"매뉴얼 {len(by_doc)}개 · 섹션 {total_sections}개 · 현재 최상위 메뉴 {len(existing)}개", 15)
    rep._current = None
    if not by_doc:
        raise ValueError("목차(## 제목)가 있는 매뉴얼이 없어 추천할 수 없습니다. 최상위 메뉴를 직접 등록해주세요.")

    existing_lines = "\n".join(f"- {c['label']}" for c in existing) or "(없음)"
    system_prompt = f"""
고객상담 챗봇 첫 화면에 나오는 최상위 메뉴 전체를 처음부터 새로 설계합니다. 최대 {MAX_CATEGORIES}개입니다.

[매뉴얼 섹션 목록]
{chr(10).join(by_doc)}

[현재 최상위 메뉴 — 참고용, 이름이 적절하면 그대로 써도 됨]
{existing_lines}

[규칙]
- 위 섹션들이 빠짐없이 들어갈 수 있도록 최상위 메뉴를 최대 {MAX_CATEGORIES}개 만드세요.
- 메뉴 하나에 섹션은 {MAX_CHILDREN}개까지만 들어갑니다. 섹션이 많은 매뉴얼은 2개 이상으로 나누세요.
  (예: '매장 관리'와 'CCTV 관리', '회원가입·로그인'과 '내 정보 관리', '고객센터'와 '문의사항')
- label은 10자 이내의 온전한 명사. '기타', '정보', '일반', '안내', '도움말' 같은 모호한 이름 금지.
- 서로 겹치는 이름(예: '매장 관리'와 '매장 · CCTV')을 만들지 마세요.
- desc는 사용자가 메뉴를 눌렀을 때 보여줄 한 문장 안내로 쓰세요.
"""
    rep.step("llm", f"AI가 최상위 메뉴 후보를 새로 만드는 중 (최대 {MAX_CATEGORIES}개)", 20)
    res = structured_catname_llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content="최상위 메뉴를 추천해주세요.")]
    )
    suggestions: list[dict] = []
    for c in res.categories:
        label = _fit_bytes(_clean_label(c.label))
        if not label or _norm(label) in _VAGUE_LABELS or any(_is_similar(label, x["label"]) for x in suggestions):
            continue
        suggestions.append({"label": label, "desc": _strip_urls(c.desc)})
    suggestions = suggestions[:MAX_CATEGORIES]
    rep.step("finish", f"추천 완료: {', '.join(x['label'] for x in suggestions) or '추천 없음'}", 100)
    rep.done("finish")
    return {**base, "suggestions": suggestions}


def replace_top_menus(menus: list[dict]) -> dict:
    """
    최상위 메뉴 전체를 관리자가 고른 목록(menus=[{label, desc}], 최대 MAX_CATEGORIES개)으로 교체합니다.
      - 이름이 같은 기존 관리자 메뉴: 그대로 유지(하위 포함), 설명(ANSWER)만 갱신
      - 목록에 없는 기존 최상위 메뉴(관리자/AI 모두): 하위까지 삭제
      - 새 이름: 비공개(USEYN='N')로 등록 — 하위메뉴를 [공개]할 때 함께 공개됨
      - 매뉴얼 전체 UPDATEYN='N' → [AI 옵션생성]으로 새 메뉴 구성에 맞게 다시 분류
    반환: { kept: [...], added: [...], removed: [...] }
    """
    cleaned = []
    for m in menus:
        label = _fit_bytes(_clean_label(m.get("label", "")))
        if label and not any(_norm(label) == _norm(x["label"]) for x in cleaned):
            cleaned.append({"label": label, "desc": (m.get("desc") or "").strip()})
    if not cleaned:
        raise ValueError("등록할 최상위 메뉴를 선택해주세요.")
    if len(cleaned) > MAX_CATEGORIES:
        raise ValueError(f"최상위 메뉴는 최대 {MAX_CATEGORIES}개까지 등록할 수 있습니다.")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kept, added, removed = [], [], []
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT NO, LABEL, AIYN FROM CHAT_MENU WHERE PNO IS NULL")
        roots = cursor.fetchall()

        keep_nos: set[int] = set()
        for vseq, m in enumerate(cleaned, start=1):
            match = next((r for r in roots if r[2] == AI_YN_MANUAL and _norm(r[1]) == _norm(m["label"])), None)
            answer = m["desc"] or _top_guide(m["label"])
            if match:
                keep_nos.add(match[0])
                kept.append(m["label"])
                cursor.execute(
                    "UPDATE CHAT_MENU SET ANSWER = :answer, VSEQ = :vseq WHERE NO = :no",
                    {"answer": answer, "vseq": vseq, "no": match[0]},
                )
            else:
                no_var = cursor.var(int)
                cursor.execute(
                    """
                    INSERT INTO CHAT_MENU (NO, PNO, STEP, LABEL, ANSWER, VSEQ, USEYN, AIYN, ANO, CDATE)
                    VALUES (CHAT_MENU_SEQ.NEXTVAL, NULL, :step, :label, :answer, :vseq, :useyn, :aiyn, NULL, :cdate)
                    RETURNING NO INTO :new_no
                    """,
                    {"step": STEP_TOP, "label": m["label"], "answer": answer, "vseq": vseq,
                     "useyn": USEYN_HIDDEN, "aiyn": AI_YN_MANUAL, "cdate": now, "new_no": no_var},
                )
                keep_nos.add(int(no_var.getvalue()[0]))
                added.append(m["label"])

        # 목록에 없는 최상위 메뉴는 하위까지 삭제 (자기참조 FK라 깊은 노드부터)
        for no, label, _ in roots:
            if no in keep_nos:
                continue
            cursor.execute(
                "SELECT NO, LEVEL FROM CHAT_MENU START WITH NO = :no CONNECT BY PRIOR NO = PNO",
                {"no": no},
            )
            nodes = sorted(cursor.fetchall(), key=lambda r: r[1], reverse=True)
            _detach_menu_refs(cursor, [n for n, _ in nodes])
            cursor.executemany("DELETE FROM CHAT_MENU WHERE NO = :1", [(n,) for n, _ in nodes])
            removed.append(label)

        # 메뉴 구성이 바뀌었으므로 매뉴얼 전체를 다시 분류 대상으로
        cursor.execute("UPDATE ATTACH_MANUAL SET UPDATEYN = 'N'")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
    return {"kept": kept, "added": added, "removed": removed}


def _assign_categories(existing: list[dict], sections: list[dict], rep: "_Reporter") -> tuple[list[dict], list[dict]]:
    """
    관리자가 만든 최상위 메뉴에 섹션을 배정합니다.
      1) 매뉴얼마다 LLM이 섹션 → 카테고리 번호 분류 (병렬)
      2) 카테고리마다 하위메뉴 최대 MAX_CHILDREN개 — 넘치면 LLM이 중요한 것만 남기고 나머지는 제외
    반환: (배정된 그룹 목록, 제외된 섹션 목록)
    """
    cats = existing
    if not cats:
        raise ValueError("최상위 메뉴가 없습니다. [+ 최상위 메뉴 추가]로 최상위 메뉴를 먼저 등록해주세요.")

    by_doc: dict[int, list[dict]] = {}
    for s in sections:
        by_doc.setdefault(s["doc"]["no"], []).append(s)

    rep.step("assign", f"섹션 {len(sections)}개를 최상위 메뉴 {len(cats)}개({', '.join(c['label'] for c in cats)})로 분류 중", 22)
    groups = [{"label": c["label"], "existingNo": c["no"], "used": c["used"], "sections": []} for c in cats]
    unmatched: list[dict] = []
    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        futures = {pool.submit(_classify_doc_sections, cats, secs): secs for secs in by_doc.values()}
        for fut in as_completed(futures):
            secs = futures[fut]
            for sec, j in zip(secs, fut.result()):
                (unmatched if j == -1 else groups[j]["sections"]).append(sec)

    # 원문 순서 유지
    order = {id(s): i for i, s in enumerate(sections)}
    for g in groups:
        g["sections"].sort(key=lambda s: order[id(s)])

    dropped = list(unmatched)
    trimmed = False
    for g in groups:
        room = max(0, MAX_CHILDREN - g["used"])
        if len(g["sections"]) <= room:
            continue
        if room == 0:
            dropped.extend(g["sections"])
            g["sections"] = []
            continue
        trimmed = True
        rep.step("trim", f"'{g['label']}' 하위메뉴가 {len(g['sections'])}개라 중요한 {room}개만 고르는 중", 32)
        picked = _pick_important(g["label"], [{"label": s["label"], "sec": s} for s in g["sections"]], room)
        keep = [p["sec"] for p in picked]
        dropped.extend(s for s in g["sections"] if not any(s is k for k in keep))
        g["sections"] = sorted(keep, key=lambda s: order[id(s)])
    if trimmed:
        rep.done("trim", "하위메뉴 5개 초과분 정리 완료")
    rep._current = None
    return [g for g in groups if g["sections"]], dropped


def _build_section(sec: dict, doc_chunks: dict[int, list[str]]) -> dict | None:
    """섹션 하나 → STEP2 노드(하위 STEP3 질문 최대 5개). 목차형은 원문 그대로, 비구조형은 RAG+LLM."""
    if sec["kind"] == "md":
        raw = sec["sec"]
        mid = {"label": sec["label"], "answer": _md_to_plain(raw["intro"]) or None, "leaves": _section_items(raw)}
        return _normalize_mid(mid)

    topic = sec["topic"]
    tree = _generate_topic_tree(topic, _topic_context(sec["doc"]["no"], doc_chunks[sec["doc"]["no"]], topic))
    if not tree:
        return None
    # 3단계 트리를 STEP2(주제) → STEP3(질문) 2단계로 펼침
    leaves: list[dict] = []
    for m in tree["children"]:
        if m["leaves"]:
            leaves.extend(m["leaves"])
        else:
            leaves.append({"label": m["label"], "answer": m["answer"]})
    answer = tree["answer"] if tree["answer"] != _top_guide(tree["label"]) else None
    return _normalize_mid({"label": sec["label"], "answer": answer, "leaves": leaves[:MAX_CHILDREN]})


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


# ── 백그라운드 작업 + 진행 상황 ────────────────────────────────────
# 생성은 요청과 분리된 스레드에서 돌고, 진행 상황은 메모리에 보관한다.
# 화면을 새로고침해도 /status 로 다시 이어서 볼 수 있다(FastAPI 재시작 시에는 초기화).
_job_lock = threading.Lock()
_job: dict = {"status": "idle"}


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


def _run_generation(rep: _Reporter) -> dict:
    """
    신규/수정 문서(UPDATEYN='N')와, 예전 방식(문서별 STEP1)으로 만들어진 문서를 대상으로
    섹션을 만들고 공용 카테고리(전체 최대 6개)에 배정해 저장합니다.
    진행률: 벡터화 0~10 / 섹션 분석 10~20 / 카테고리 이름(LLM)·배정(임베딩) 20~35 / 하위메뉴 생성 35~92 / 저장 92~100
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        _ensure_ano_column(cursor)
        legacy_nos = _has_legacy_menus(cursor)
    finally:
        cursor.close()
        connection.close()

    docs = [d for d in get_manual_docs() if d["updateYn"] == "N" or d["no"] in legacy_nos]
    if not docs:
        raise ValueError("새로 등록되거나 수정된 매뉴얼 문서가 없습니다.")
    legacy_cnt = sum(1 for d in docs if d["no"] in legacy_nos and d["updateYn"] == "Y")
    rep.step("target", f"대상 문서 {len(docs)}개 확인" + (f" (예전 구조 재생성 {legacy_cnt}개 포함)" if legacy_cnt else ""), 1)

    # 1) 벡터화 (0~10) — 업로드 때는 벡터화하지 않으므로 여기서 처리 (AI자유상담 검색 + 비구조 문서 RAG 재료)
    vectorize_failed_docs = []
    for i, doc in enumerate(docs):
        key = f"vec-{doc['no']}"
        rep.step(key, f"[{doc['filename']}] 벡터화 확인 중", 1 + 9 * i / len(docs))
        if _is_vectorized(doc["no"]):
            rep.done(key, f"[{doc['filename']}] 이미 벡터화됨")
            rep._current = None
            continue
        rep.step(key, f"[{doc['filename']}] 벡터화 중 (AI자유상담 검색 반영)")
        try:
            _add_doc_to_vectorstore(doc["uploadPath"], doc["no"])
            rep.done(key, f"[{doc['filename']}] 벡터화 완료", 1 + 9 * (i + 1) / len(docs))
        except Exception:
            vectorize_failed_docs.append({"no": doc["no"], "filename": doc["filename"]})
            rep.fail(key, f"[{doc['filename']}] 벡터화 실패")
        rep._current = None

    # 2) 섹션 분석 (10~20) — 목차가 있으면 목차 섹션 그대로, 없으면 벡터DB 청크로 LLM이 주제 분석
    sections: list[dict] = []
    doc_chunks: dict[int, list[str]] = {}
    for i, doc in enumerate(docs):
        key = f"plan-{doc['no']}"
        pct = 10 + 10 * (i + 1) / len(docs)
        rep.step(key, f"[{doc['filename']}] 섹션 분석 중", 10 + 10 * i / len(docs))
        structure = _read_doc_structure(doc)
        if structure:
            for sec in structure:
                hint = " / ".join(_clean_label(it["title"]) for it in sec["items"][:3])
                sections.append({"doc": doc, "kind": "md", "sec": sec, "label": _clean_label(sec["title"]), "hint": hint})
            rep.done(key, f"[{doc['filename']}] 목차 섹션 {len(structure)}개", pct)
        else:
            chunks = _load_doc_chunks(doc)
            if not chunks:
                rep.fail(key, f"[{doc['filename']}] 읽을 수 있는 내용이 없음", pct)
                rep._current = None
                continue
            doc_chunks[doc["no"]] = chunks
            topics = _plan_topics(doc, chunks)
            for t in topics:
                sections.append({"doc": doc, "kind": "rag", "topic": t, "label": t["label"], "hint": t["query"]})
            rep.done(key, f"[{doc['filename']}] AI 분석 주제 {len(topics)}개", pct)
        rep._current = None

    if not sections:
        raise ValueError("업로드된 문서에서 읽을 수 있는 내용이 없습니다.")
    target_nos = {s["doc"]["no"] for s in sections}

    # 3) 공용 카테고리 배정 (20~35) — 모든 대상 문서의 섹션을 한 번에 배정
    connection = get_connection()
    cursor = connection.cursor()
    try:
        existing = _load_existing_categories(cursor, target_nos)
    finally:
        cursor.close()
        connection.close()
    groups, dropped = _assign_categories(existing, sections, rep)
    names = ", ".join(f"{g['label']}({len(g['sections'])})" for g in groups)
    rep.done("assign", f"분류 완료: {names}", 35)
    rep._current = None
    if dropped:
        rep.info("dropped", f"안내: 메뉴당 하위메뉴 최대 {MAX_CHILDREN}개라 제외된 섹션 {len(dropped)}개 — {', '.join(s['label'] for s in dropped)}")

    # 4) 하위메뉴 생성 (35~92) — 목차형은 즉시, 질문 5개 초과/비구조형만 LLM
    jobs = [(g, sec) for g in groups for sec in g["sections"]]
    built: dict[int, dict] = {}  # id(sec) -> STEP2 트리
    failed_docs: set[int] = set()

    def work(order: int, sec: dict) -> dict | None:
        rep.start(f"sec-{order}", f"'{sec['label']}' 하위메뉴 생성 중")
        return _build_section(sec, doc_chunks)

    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        futures = {pool.submit(work, order, sec): (order, sec) for order, (_, sec) in enumerate(jobs)}
        for done_cnt, fut in enumerate(as_completed(futures), start=1):
            order, sec = futures[fut]
            pct = 35 + 57 * done_cnt / len(jobs)
            try:
                mid = fut.result()
                if mid:
                    built[id(sec)] = mid
                    rep.done(f"sec-{order}", f"'{sec['label']}' 완료 ({done_cnt}/{len(jobs)})", pct)
                else:
                    rep.done(f"sec-{order}", f"'{sec['label']}' 내용이 없어 건너뜀 ({done_cnt}/{len(jobs)})", pct)
            except Exception as e:
                failed_docs.add(sec["doc"]["no"])
                print(f"⚠ 섹션 생성 실패({sec['label']}): {e}")
                rep.fail(f"sec-{order}", f"'{sec['label']}' 생성 실패, 건너뜀 ({done_cnt}/{len(jobs)})", pct)

    # 5) 저장 (92~100)
    rep.step("save", "생성된 옵션 저장 중", 93)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 생성이 실패한 섹션이 있는 문서는 기존 메뉴를 지우지 않고 'N'으로 남겨 다음에 다시 생성
    replace_nos = target_nos - failed_docs
    preview_categories = []
    connection = get_connection()
    cursor = connection.cursor()
    try:
        for doc_no in replace_nos:
            _delete_ai_menus_of_doc(cursor, doc_no)

        for g in groups:
            mids = [(sec, built[id(sec)]) for sec in g["sections"]
                    if id(sec) in built and sec["doc"]["no"] in replace_nos]
            if not mids:
                continue

            cat_no = g["existingNo"]
            cursor.execute("SELECT LABEL, ANSWER FROM CHAT_MENU WHERE NO = :no", {"no": cat_no})
            row = cursor.fetchone()
            if row is None:
                continue  # 생성 도중 관리자가 그 최상위 메뉴를 지운 경우
            cat_label, cat_answer = row[0], _lob_str(row[1])

            # 최상위 메뉴와 제목이 사실상 같은 섹션은 하위메뉴로 두지 않고 STEP1 설명으로 사용
            # (그 섹션에 질문이 있으면 질문들을 STEP2로 한 단계 올림).
            # 관리자가 설명을 직접 적어둔 메뉴는 덮어쓰지 않는다.
            can_set_answer = not cat_answer or cat_answer == _top_guide(cat_label)
            overview, rest = None, []
            for sec, mid in mids:
                if overview is None and can_set_answer and _is_similar(mid["label"], cat_label):
                    if mid["answer"] and mid["answer"] != _top_guide(mid["label"]):
                        overview = mid["answer"]
                    rest.extend((sec, {"label": l["label"], "answer": l["answer"], "leaves": []}) for l in mid["leaves"])
                    continue
                rest.append((sec, mid))
            rest = rest[: max(0, MAX_CHILDREN - g["used"])]
            answer_only = not rest
            if overview:
                cat_answer = overview

            if overview:
                cursor.execute("UPDATE CHAT_MENU SET ANSWER = :answer WHERE NO = :no", {"answer": cat_answer, "no": cat_no})

            cursor.execute("SELECT NVL(MAX(VSEQ), 0) FROM CHAT_MENU WHERE PNO = :pno", {"pno": cat_no})
            mid_vseq = int(cursor.fetchone()[0])
            p_mids = []
            for sec, mid in rest:
                mid_vseq += 1
                doc_no = sec["doc"]["no"]
                mid_no = _insert_menu(cursor, pno=cat_no, step=STEP_MID, label=mid["label"], answer=mid["answer"],
                                      vseq=min(mid_vseq, 999), ano=doc_no, now=now)
                p_leaves = []
                for l_i, leaf in enumerate(mid["leaves"], start=1):
                    leaf_no = _insert_menu(cursor, pno=mid_no, step=STEP_LEAF, label=leaf["label"],
                                           answer=leaf["answer"], vseq=l_i, ano=doc_no, now=now)
                    p_leaves.append({"no": leaf_no, "label": leaf["label"], "answer": leaf["answer"]})
                p_mids.append({"no": mid_no, "label": mid["label"], "answer": mid["answer"],
                               "leaves": p_leaves, "filename": sec["doc"]["filename"]})
            preview_categories.append({"no": cat_no, "label": cat_label, "answer": cat_answer,
                                       "answerOnly": answer_only, "children": p_mids})

        _cleanup_empty_categories(cursor)
        for doc_no in replace_nos:
            cursor.execute("UPDATE ATTACH_MANUAL SET UPDATEYN = 'Y' WHERE NO = :no", {"no": doc_no})
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    skipped_docs = [d["filename"] for d in docs if d["no"] in failed_docs]
    summary = f"완료: 카테고리 {len(preview_categories)}개에 하위메뉴 {sum(len(c['children']) for c in preview_categories)}개 생성"
    if skipped_docs:
        summary += f" (일부 실패로 기존 메뉴 유지: {', '.join(skipped_docs)})"
    rep.step("finish", summary, 100)
    rep.done("finish")
    return {
        "categories": preview_categories,
        "vectorizeFailedDocs": vectorize_failed_docs,
        "skippedDocs": skipped_docs,
        "droppedSections": [f"{s['label']} ({s['doc']['filename']})" for s in dropped],
    }


_JOB_RUNNERS = {
    "generate": ("AI 옵션생성", lambda rep: _run_generation(rep)),
    "suggest": ("최상위 메뉴 추천", lambda rep: suggest_categories(rep)),
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
        job = _job
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
    global _job
    with _job_lock:
        if _job.get("status") == "running":
            already = True
        else:
            already = False
            _job = _new_job(kind)
            job = _job
    if not already:
        threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"chatmenu-{kind}").start()
    return get_generate_job_status()


def start_generate_job() -> dict:
    """AI 옵션생성을 백그라운드로 시작합니다."""
    return _start_job("generate")


def start_suggest_job() -> dict:
    """최상위 메뉴 AI 추천을 백그라운드로 시작합니다(로컬 CPU에서 1~2분)."""
    return _start_job("suggest")


def clear_generate_job() -> dict:
    """완료/실패한 작업 기록을 지웁니다(관리자가 진행 패널을 닫을 때). 진행 중이면 무시."""
    global _job
    with _job_lock:
        if _job.get("status") != "running":
            _job = {"status": "idle"}
    return get_generate_job_status()


def generate_menu_from_docs() -> dict:
    """동기 실행 버전(기존 API 호환용). 진행 상황은 기록만 하고 버립니다."""
    return _run_generation(_Reporter(_new_job()))


# =====================================================================
# 벡터화 재시도 (AI생성 응답에 vectorizeFailedDocs가 있을 때, 프론트가
# "벡터화 재시도" 버튼으로 이 함수만 따로 호출)
# =====================================================================

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


# =====================================================================
# 공개 전환 (관리자가 미리보기 검토 후 [공개] 클릭 시)
# =====================================================================

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




