# python -m modules.manual_retriever

"""
modules/manual_retriever.py

매뉴얼 벡터 DB(ChromaDB)에서 질문과 유사한 문서를 검색합니다.
실습(gabia_retriever.py)과 동일한 구조이되, 임베딩만 Ollama로 교체했습니다.

scripts/ingest_manual.py로 미리 적재해둔 data/chromadb_manual을 그대로 읽습니다.
"""

from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

from core.llm_client import get_llm

import os

# modules/manual_retriever.py 기준으로, 형제 폴더인 chatbot/data/chromadb_manual을 가리킴
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # .../team2_fastapi_v1/modules
PERSIST_DIRECTORY = os.path.join(BASE_DIR, "..", "chatbot", "data", "chromadb_manual")

_vectorstore = None  # 모듈 로드 시 한 번만 연결, 매 요청마다 재연결하지 않음


def _get_embedding():
    # 주소를 안 주면 같은 컴퓨터의 Ollama(localhost:11434)를 씀 (gemma와 동일)
    return OllamaEmbeddings(model="bge-m3")


def _get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(
            persist_directory=PERSIST_DIRECTORY,
            embedding_function=_get_embedding(),
        )
    return _vectorstore


def safe_similarity_search(vectorstore, query: str, k: int, where: dict | None = None) -> list[tuple[str, float]]:
    """
    벡터DB 유사도 검색 — (본문, 관련도 점수) 목록.

    LangChain의 similarity_search는 결과마다 Document를 만드는데, 벡터DB에 본문(document)이
    비어 있는(None) 항목이 섞여 있으면 "1 validation error for Document / page_content"로
    검색 전체가 실패한다(H200 벡터DB에서 실제 발생). 그래서 ChromaDB에 직접 질의하고
    본문이 없는 항목은 건너뛴다. 관련도 점수는 LangChain과 같은 함수로 계산(기준값 동일).
    """
    collection = vectorstore._collection
    total = collection.count()
    if total == 0:
        return []
    embedding = vectorstore._embedding_function.embed_query(query)
    res = collection.query(
        query_embeddings=[embedding],
        n_results=min(total, k * 2),  # 빈 항목을 건너뛰어도 k개를 채울 수 있게 여유 있게 조회
        where=where,
        include=["documents", "distances"],
    )
    to_score = vectorstore._select_relevance_score_fn()
    hits = [
        (text, to_score(dist))
        for text, dist in zip(res["documents"][0], res["distances"][0])
        if text  # 본문 없는 항목 제외
    ]
    return hits[:k]


def search_manual(query: str, k: int = 3):
    """
    질문과 유사한 매뉴얼 청크를 k개 검색해서 반환합니다.
    score까지 같이 돌려줘서, 검색은 됐지만 관련성이 낮은 경우를 chat_rag_langgraph.py에서 걸러냅니다.
    """
    return [{"content": text, "score": score} for text, score in safe_similarity_search(_get_vectorstore(), query, k)]


if __name__ == "__main__":
    # 단독 테스트용 — python -m modules.manual_retriever
    querys = ["구독권 취소하고 싶어요", "환불은 어떻게 계산되나요?", "CCTV 화면이 안 나와요"]
    for q in querys:
        print("=" * 70)
        print(f"[질문] {q}")
        results = search_manual(q)
        for r in results:
            print(f"  score={r['score']:.3f} | {r['content'][:80]}...")