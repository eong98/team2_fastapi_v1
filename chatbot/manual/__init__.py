"""
chatbot/manual/ (옵션형 메뉴 자동생성)

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

파일 구성:
  config.py      옵션메뉴 자동생성 — 경로, CHAT_MENU 코드값, 개수 제한 같은 설정값.
  llm.py         옵션메뉴 자동생성 — 전용 LLM, 구조화 출력(JSON) 안전장치, LLM 응답 스키마.
  markdown.py    매뉴얼(md) 목차 파싱과 본문 정리 — '## 섹션 / ### 질문' 구조를 메뉴 재료로 바꿈.
  rules.py       메뉴 트리 규칙 — 하위메뉴 합치기/5개 제한, 긴 제목 요약, 제목 유사도, 중요 질문 선택.
  vectors.py     매뉴얼 벡터DB(ChromaDB, bge-m3) — 벡터화/삭제/조회/유사도 검색. AI 상담 검색과 같은 DB.
  db.py          CHAT_MENU 저장·삭제·공개 — 문서(ANO)별 AI 메뉴 교체, 삭제 전 세션/로그 참조 해제.
  progress.py    백그라운드 작업 진행 상태 — 진행률·단계 로그 기록기(_Reporter)와 현재 작업 상태.
  docs.py        첨부 매뉴얼(ATTACH_MANUAL) 등록/수정/삭제/목록. 업로드 때는 파일 저장만, 벡터화는 옵션생성 때.
  rag_topic.py   목차(## 제목)가 없는 매뉴얼 — 벡터DB 청크로 LLM이 주제를 나누고 주제별 하위메뉴 생성(RAG).
  category.py    최상위 메뉴 — AI 추천, 관리자가 고른 목록으로 교체, 매뉴얼 섹션을 최상위 메뉴로 분류.
  generate.py    AI 옵션생성 파이프라인 — 벡터화 → 섹션 분석 → 분류 → 하위메뉴 생성 → 제목 요약 → 저장.
  job.py         백그라운드 작업 시작/조회/정리 — 옵션생성·최상위 메뉴 추천을 스레드로 실행 (동시에 1개).
  router.py      /api/chatbot/manual-doc/* API
"""
