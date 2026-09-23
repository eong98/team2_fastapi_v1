# python -m chatbot.ingest_manual

"""

ingest_manual.py

매뉴얼 md 파일들(예: subscription_manual.md, qa_notice_manual_v2.md)을 읽어서
청크 단위로 쪼갠 뒤, Ollama 임베딩으로 벡터화하여 ChromaDB에 저장합니다.

실습(rag_embedding_marketing.py)과 동일한 구조이되, 다음을 교체했습니다:
  - OpenAIEmbeddings → OllamaEmbeddings (nomic-embed-text)
  - PyPDFLoader → 순수 텍스트(.md) 로더 (매뉴얼이 md 파일이므로)
  - persist_directory → 프로젝트 공통 경로(data/chromadb_manual)

사용법 (1회성 실행, 매뉴얼 문서가 바뀔 때마다 다시 실행):
    1. 매뉴얼 md 파일들을 data/manuals/ 폴더에 넣습니다.
       (예: data/manuals/subscription_manual.md, data/manuals/qa_notice_manual.md)
    2. Ollama가 떠있는지 확인:
         ollama pull nomic-embed-text
    3. 실행:
         python scripts/ingest_manual.py
"""

import glob
import os

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

from core.llm_client import get_llm


# 이 스크립트 파일(chatbot/ingest_manual.py) 기준으로 경로를 고정
# → 저장소 루트에서 실행하든, chatbot 폴더 안에서 실행하든 항상 같은 곳을 가리킴
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MANUAL_DIR = os.path.join(BASE_DIR, "data", "manuals")
PERSIST_DIRECTORY = os.path.join(BASE_DIR, "data", "chromadb_manual")


def get_embedding():
    """
    임베딩 모델도 LLM과 동일하게 H200/로컬을 자동 판별합니다.
    core.llm_client.get_llm()과 같은 호스트 판별 로직(is_h200)을 재사용합니다.
    """
    base_url = "http://localhost:11434"  # Ollama 기본 포트, H200/로컬 동일 포트 가정

    return OllamaEmbeddings(model="bge-m3", base_url=base_url)


def load_manual_documents():
    """data/manuals/ 안의 모든 .md 파일을 로드합니다."""
    md_files = glob.glob(os.path.join(MANUAL_DIR, "*.md"))

    if not md_files:
        raise FileNotFoundError(
            f"{MANUAL_DIR} 안에 .md 파일이 없습니다. 매뉴얼 문서를 이 폴더에 넣어주세요."
        )

    docs = []
    for path in md_files:
        loader = TextLoader(path, encoding="utf-8")
        docs.extend(loader.load())
        print(f"  로드됨: {path}")

    return docs


def main():
    embedding = get_embedding()

    # 1) 문서 로드
    docs = load_manual_documents()
    print(f"-> 총 {len(docs)}개 문서 로드 완료")

    # 2) 문서 쪼개기
    # 매뉴얼은 "## 섹션" 단위로 이미 자체 완결형으로 작성되어 있어서,
    # chunk_size를 섹션 하나 정도(800~1000자)로 잡고 overlap은 조금만 줍니다.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n## ", "\n\n", "\n", " ", ""],  # 섹션 제목(##) 기준으로 우선 분할
    )
    splits = splitter.split_documents(docs)
    print(f"-> {len(splits)}개 청크로 분할 완료")

    # 3) Chroma DB 생성 + 저장
    if not os.path.exists(PERSIST_DIRECTORY):
        print("-> 새 Chroma 저장소 생성")
        vectorstore = Chroma.from_documents(
            documents=splits,
            embedding=embedding,
            persist_directory=PERSIST_DIRECTORY,
        )
    else:
        print("-> 기존 Chroma 저장소에 추가 적재")
        vectorstore = Chroma(
            persist_directory=PERSIST_DIRECTORY,
            embedding_function=embedding,
        )
        vectorstore.add_documents(splits)

    print(f"✅ 벡터 DB 생성/갱신 완료 — {PERSIST_DIRECTORY}")


if __name__ == "__main__":
    main()
