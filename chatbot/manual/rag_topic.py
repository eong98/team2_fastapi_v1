"""
목차(## 제목)가 없는 매뉴얼 — 벡터DB 청크로 LLM이 주제를 나누고 주제별 하위메뉴 생성(RAG).
"""

import os

from langchain_core.messages import HumanMessage, SystemMessage

from chatbot.manual.config import MAX_CHILDREN, MAX_TOPICS_PER_DOC, TOPIC_CONTEXT_CHUNKS
from chatbot.manual.llm import structured_top_llm, structured_topic_llm
from chatbot.manual.rules import _normalize_top, _strip_urls
from chatbot.manual.vectors import _search_doc_chunks


def _chunk_digest(chunks: list[str]) -> str:
    """주제 계획용 요약본 — 청크마다 제목줄과 앞부분만 짧게 보여준다(입력 토큰 절약)."""
    lines = []
    for i, chunk in enumerate(chunks):
        heads = [ln.strip() for ln in chunk.splitlines() if ln.strip().startswith("#")]
        body = " ".join(ln.strip() for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("#"))
        lines.append(f"[{i}] {' / '.join(heads)[:150]} :: {_strip_urls(body)[:160]}")
    return "\n".join(lines)


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
