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
    base_url = "http://localhost:11434"
    
    return OllamaEmbeddings(model="bge-m3", base_url=base_url)


def _get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(
            persist_directory=PERSIST_DIRECTORY,
            embedding_function=_get_embedding(),
        )
    return _vectorstore


def search_manual(query: str, k: int = 3):
    """
    질문과 유사한 매뉴얼 청크를 k개 검색해서 반환합니다.
    """
    vectorstore = _get_vectorstore()

    # score까지 같이 받아서, 검색이 되긴 했지만 실제로는 관련성이 낮은 경우를
    # 나중에(chat_rag.py) 걸러낼 수 있게 합니다.
    results = vectorstore.similarity_search_with_relevance_scores(query, k=k)

    docs = []
    for doc, score in results:
        docs.append({"content": doc.page_content, "score": score})

    return docs


if __name__ == "__main__":
    # 단독 테스트용 — python -m modules.manual_retriever
    querys = ["구독권 취소하고 싶어요", "환불은 어떻게 계산되나요?", "CCTV 화면이 안 나와요"]
    for q in querys:
        print("=" * 70)
        print(f"[질문] {q}")
        results = search_manual(q)
        for r in results:
            print(f"  score={r['score']:.3f} | {r['content'][:80]}...")