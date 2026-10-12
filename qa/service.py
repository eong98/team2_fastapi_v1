"""
qa/service.py

1:1 문의 AI 자동 답변.

챗봇 AI 상담(modules/chat_rag_langgraph.py)과 같은 질문 판별·매뉴얼 검색·답변 프롬프트를
그대로 가져다 써서, 상담봇과 같은 내용으로 답합니다. 챗봇 그래프 자체는 건드리지 않고
필요한 노드 함수와 프롬프트만 재사용합니다.

모르는 건 답하지 않습니다 — 아래 경우는 answered=False
  - 인사·뜻 모를 입력·관리자 연결 요청 (LLM 호출 없음)
  - 매뉴얼 검색 결과가 문턱(RELEVANCE_THRESHOLD)을 넘는 게 하나도 없음 (LLM 호출 없음)
  - AI가 매뉴얼 내용으로 답하지 않았거나(fromManual=False) 관리자 확인이 필요하다고 판단(needsAdmin=True)
  - 질문의 일부라도 정보를 찾지 못함 (complete=False 또는 답변에 "찾지 못했습니다"·"연결해 드릴까요" 같은 문구)
    → 게시판 답변은 채팅처럼 이어서 물어볼 수 없으므로, 일부만 답하고 나머지를 모른다고 하는 답변은 등록하지 않음
  - 답변이 비었거나 폴백 문구
"""

from pydantic import Field
from langchain_core.messages import HumanMessage, SystemMessage

from chatbot.text_clean import scrub_internal, strip_urls
from modules.chat_rag_langgraph import (
    FALLBACK_ANSWER,
    MIN_ANSWER_LENGTH,
    ChatAnswer,
    build_system_prompt,
    check_greeting_node,
    llm,
    search_node,
)


class QaAnswer(ChatAnswer):
    """챗봇 응답 스키마(answer, needsAdmin)에 '매뉴얼로 답했는지'만 추가."""

    fromManual: bool = Field(
        description=(
            "답변 내용을 [참고 자료]에 있는 정보로 작성했으면 true. "
            "인사, 일상대화·무관한 질문 안내, 정보를 찾지 못했다는 안내, 관리자 연결 안내면 false"
        )
    )


    complete: bool = Field(
        description=(
            "질문에서 물어본 모든 내용을 [참고 자료]로 답했으면 true. "
            "질문 중 일부라도 정보를 찾지 못했거나, 찾지 못했다는 안내·담당자 연결 안내를 답변에 넣었으면 false"
        )
    )


structured_qa_llm = llm.with_structured_output(QaAnswer)

# 답변에 이런 말이 들어 있으면 "정보를 찾지 못한 부분이 있다"는 뜻 → 등록하지 않음
# (AI가 complete를 true로 잘못 표시한 경우를 한 번 더 걸러 내는 안전장치)
UNKNOWN_PHRASES = (
    "찾지 못했",
    "찾을 수 없",
    "확인되지 않",
    "확인할 수 없",
    "정보가 없",
    "안내해 드리기 어렵",
    "답변드리기 어렵",
    "답변해 드리기 어렵",
    "연결해 드릴까요",
    "담당자",
    "관리자에게",
    "관리자 문의",
)


def _mentions_unknown(answer: str) -> bool:
    """답변에 '정보를 찾지 못했다'·'담당자 연결' 같은 문구가 있는지"""
    return any(phrase in answer for phrase in UNKNOWN_PHRASES)

NOT_ANSWERED = {"answered": False, "answer": ""}


def answer_for_qa(title: str, content: str) -> dict:
    """
    문의 제목·내용으로 AI 답변을 만듭니다.

    반환값: { "answered": bool, "answer": str }  (answered=False면 answer는 빈 문자열)
    """
    message = "\n".join(part for part in [(title or "").strip(), (content or "").strip()] if part)
    if not message:
        return NOT_ANSWERED

    # 1. 질문 판별 — 인사·뜻 모를 입력·관리자 연결 요청은 답하지 않음
    route = check_greeting_node({"message": message, "history": []})["route"]
    if route != "question":
        print(f"[QA 자동답변] answered=False (route={route})")
        return NOT_ANSWERED

    # 2. 매뉴얼 검색 — 관련 내용이 없으면 LLM 호출 없이 끝
    context = search_node({"message": message, "history": []})["docs_context"]
    if context == "없음":
        print("[QA 자동답변] answered=False (관련 매뉴얼 없음)")
        return NOT_ANSWERED

    # 3. 챗봇과 같은 프롬프트로 답변 생성 + 매뉴얼로 답했는지 판단
    result = structured_qa_llm.invoke(
        [SystemMessage(content=build_system_prompt(context)), HumanMessage(content=message)]
    )
    answer = scrub_internal(strip_urls(result.answer or "")).strip()

    answered = (
        result.fromManual
        and result.complete
        and not result.needsAdmin
        and not _mentions_unknown(answer)
        and len(answer) >= MIN_ANSWER_LENGTH
        and answer != FALLBACK_ANSWER
    )
    print(
        f"[QA 자동답변] answered={answered} fromManual={result.fromManual} complete={result.complete} "
        f"needsAdmin={result.needsAdmin} unknownPhrase={_mentions_unknown(answer)}"
    )
    return {"answered": answered, "answer": answer} if answered else NOT_ANSWERED
