"""
매뉴얼 벡터DB(ChromaDB, bge-m3) — 벡터화/삭제/조회/유사도 검색. AI 상담 검색과 같은 DB.
"""

import json
import os
import urllib.request

from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from modules.manual_retriever import safe_similarity_search

from chatbot.manual.config import CHROMA_PERSIST_DIRECTORY, EMBED_BASE_URL, EMBED_MODEL


def _get_chroma_embedding():
    """modules/manual_retriever.py의 _get_embedding()과 동일한 설정(bge-m3)."""
    return OllamaEmbeddings(model=EMBED_MODEL, base_url=EMBED_BASE_URL)


def _ensure_embedding_model() -> None:
    """
    Ollama에 bge-m3 임베딩 모델이 설치되어 있는지 확인합니다.
    벡터DB가 bge-m3로 만들어져 있어 다른 모델로는 대체할 수 없으므로, 없으면 설치 안내와 함께 중단.
    """
    try:
        with urllib.request.urlopen(f"{EMBED_BASE_URL}/api/tags", timeout=5) as res:
            names = [m.get("name", "") for m in json.load(res).get("models", [])]
    except Exception as e:
        raise RuntimeError(f"Ollama({EMBED_BASE_URL})에 연결할 수 없습니다: {e}")
    if not any(n.split(":")[0] == EMBED_MODEL for n in names):
        raise RuntimeError(
            f"{EMBED_MODEL} 임베딩 모델이 없습니다. 서버에서 'ollama pull {EMBED_MODEL}'을 실행해주세요."
        )


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


_vectorstore_cache = None


def _get_vectorstore():
    global _vectorstore_cache
    if _vectorstore_cache is None:
        _vectorstore_cache = Chroma(
            persist_directory=CHROMA_PERSIST_DIRECTORY,
            embedding_function=_get_chroma_embedding(),
        )
    return _vectorstore_cache


def _purge_empty_chunks() -> int:
    """
    본문(document)이 비어 있는(None) 벡터 항목을 삭제합니다. 이런 항목이 있으면 AI 상담 검색이
    "validation error for Document / page_content"로 실패합니다(H200 벡터DB에서 발생).
    [AI 옵션생성] 벡터화 단계에서 매번 호출해 자동으로 정리합니다.
    반환: 삭제한 개수
    """
    if not os.path.exists(CHROMA_PERSIST_DIRECTORY):
        return 0
    collection = _get_vectorstore()._collection
    res = collection.get(include=["documents"])
    empty_ids = [i for i, text in zip(res.get("ids") or [], res.get("documents") or []) if not text]
    if empty_ids:
        collection.delete(ids=empty_ids)
    return len(empty_ids)


def _load_doc_chunks(doc: dict) -> list[str]:
    """
    벡터화된 이 문서의 청크를 원문 순서대로 가져옵니다.
    벡터DB에 없으면(벡터화 실패) 파일을 직접 같은 규칙으로 잘라서 씁니다.
    """
    try:
        res = _get_vectorstore().get(
            where={"attach_manual_no": doc["no"]}, include=["documents", "metadatas"]
        )
        # 본문이 비어 있는(None) 항목은 제외 (벡터DB에 섞여 있으면 이후 처리에서 오류)
        pairs = [(t, m) for t, m in zip(res.get("documents") or [], res.get("metadatas") or []) if t]
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
        hits = safe_similarity_search(_get_vectorstore(), query, k, where={"attach_manual_no": doc_no})
        return [text for text, _ in hits]
    except Exception as e:
        print(f"⚠ 유사도 검색 실패(문서 no={doc_no}): {e}")
        return []
