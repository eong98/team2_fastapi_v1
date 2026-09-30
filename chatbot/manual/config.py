"""
옵션메뉴 자동생성 — 경로, CHAT_MENU 코드값, 개수 제한 같은 설정값.
"""

import os


# chatbot/ 폴더 기준 (실행 위치(cwd)와 무관하게 항상 같은 곳)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


UPLOAD_DIR = os.path.join(BASE_DIR, "data", "manual_docs")


# AI자유상담(modules/ingest_manual.py, modules/manual_retriever.py)이 쓰는
# 것과 동일한 ChromaDB 경로. 여기에 합류시켜야 search_manual()이 같이 찾는다.
CHROMA_PERSIST_DIRECTORY = os.path.join(BASE_DIR, "data", "chromadb_manual")


STEP_TOP = 1


STEP_MID = 2


STEP_LEAF = 3


USEYN_HIDDEN = "N"


USEYN_VISIBLE = "Y"


AI_YN_AI_MANAGED = "Y"   # AI가 생성한 노드 (재생성 시 삭제 대상)


AI_YN_MANUAL = "N"       # 관리자가 추가/수정한 노드 (재생성 시 항상 보존)


EMBED_MODEL = "bge-m3"


EMBED_BASE_URL = "http://localhost:11434"


MAX_CHILDREN = 5          # 한 부모 아래 하위메뉴 최대 개수


MAX_TOPICS_PER_DOC = 6    # 문서 하나에서 만들 최상위(STEP1) 메뉴 최대 개수


TOPIC_CONTEXT_CHUNKS = 4  # 주제 하나 생성에 넘길 청크 최대 개수 (num_ctx 안에 들어가게)


# 주제별 LLM 병렬 호출 수. CPU 추론이면 동시에 돌려도 빨라지지 않으므로 GPU 서버에서만 늘릴 것
LLM_WORKERS = int(os.getenv("MENU_LLM_WORKERS", "3"))


# 최상위 메뉴는 관리자가 직접 등록한다(AIYN='N', 최대 MAX_CATEGORIES개). AI는 후보 이름만 추천하고
# (suggest_categories), [AI 옵션생성] 때는 매뉴얼 섹션을 관리자 메뉴 중 하나로 분류만 한다.
#   STEP1 = 관리자 최상위 메뉴 (모든 매뉴얼 공용, 문서 소유 아님)
#   STEP2 = 매뉴얼의 섹션/주제 (AIYN='Y', ANO=문서번호)
#   STEP3 = 질문 (AIYN='Y', ANO=문서번호)
# 문서를 수정/삭제하면 그 문서의 STEP2·3만 교체/삭제된다.
MAX_CATEGORIES = 6


LABEL_MAX_BYTES = 100      # CHAT_MENU.LABEL VARCHAR2(100 BYTE)


LABEL_SUMMARY_BYTES = 90   # 이보다 긴 제목은 요약 (한글 약 30자, 여유 포함)
