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
  - 답할 수 있는 부분이 하나도 없음 (답변이 비었거나 폴백 문구)

질문 중 일부만 매뉴얼에 있으면, 답할 수 있는 부분만 답하고 나머지는 언급하지 않습니다.
(게시판 답변은 채팅처럼 이어서 물어볼 수 없으므로 "찾지 못했습니다", "담당자에게 연결해 드릴까요"
 같은 문장은 넣지 않음 — 프롬프트로 지시하고, 그래도 들어오면 그 문장만 지움)
"""

import re

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
            "질문 중 일부라도 [참고 자료]에 있는 정보로 답했으면 true. "
            "[참고 자료]로 답한 내용이 하나도 없으면(인사, 무관한 질문 안내, 관리자 연결 안내뿐이면) false"
        )
    )


structured_qa_llm = llm.with_structured_output(QaAnswer)

# 게시판 문의 답변 규칙 — 챗봇 프롬프트(build_system_prompt) 뒤에 덧붙임
QA_RULES = """

[게시판 문의 답변 규칙 — 위 지침보다 우선]
- 이 답변은 채팅이 아니라 1:1 문의 게시판에 등록됩니다. 고객이 바로 이어서 질문할 수 없습니다.
- 질문에 여러 내용이 있으면, [참고 자료]로 답할 수 있는 부분만 답하세요.
- 답할 수 없는 부분은 아예 언급하지 마세요.
  "정보를 찾지 못했습니다", "담당자에게 연결해 드릴까요", "관리자에게 문의해 주세요" 같은 문장을 쓰지 마세요.
- 일부라도 [참고 자료]로 답했으면 fromManual=true, needsAdmin=false 로 표시하세요.
- 답할 수 있는 부분이 하나도 없을 때만 fromManual=false 로 표시하세요.
- 고객은 **이미 이 문의를 접수한 상태**입니다. "문의를 남겨 주세요", "[문의하기] 버튼을 눌러 주세요",
  "고객센터 1:1 문의로 요청해 주세요"처럼 다시 문의하라는 안내를 쓰지 마세요.
  [참고 자료]에 "문의로 요청하면 처리된다"고 되어 있는 일(탈퇴, 정지 해제, CCTV 등록·변경 등)은
  "화면에서 직접 할 수는 없고 담당자가 처리합니다. 이 문의로 접수되었으니 담당자가 확인한 뒤 답변드리겠습니다."처럼
  지금 남긴 문의로 처리된다는 뜻으로 바꿔 쓰세요.
"""

# 이런 말이 들어 있는 문장은 "못 찾은 부분에 대한 안내"라서 답변에서 지움
# (프롬프트로 쓰지 말라고 했지만 그래도 들어오는 경우를 걸러 내는 안전장치)
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
    "담당자에게",
    "담당자 연결",
    "관리자에게",
    "관리자 문의",
)

# 이미 접수된 문의인데 "문의를 남겨 달라"고 다시 시키는 문장 — 지우고 아래 맺음말로 바꿈
# (프롬프트에서 막았지만 매뉴얼 문장을 그대로 옮기는 경우를 걸러 내는 안전장치)
REASK_PATTERN = re.compile(
    r"(문의하기\]?\s*(버튼|를|을)|문의(사항)?(를|을)\s*(남겨|남기|작성|접수|등록)|문의(사항)?(로|으로)\s*(요청|남겨|알려|연락|접수)"
    r"|고객센터(로|에)\s*(문의|요청|연락))"
)
REASK_CLOSING = "남겨 주신 이 문의는 담당자가 확인한 뒤 답변드리겠습니다."

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _drop_unknown_sentences(answer: str) -> str:
    """답변에서 '정보를 찾지 못했다'·'담당자 연결' 문장만 지우고 나머지는 그대로 둡니다."""
    kept_lines = []
    for line in answer.splitlines():
        sentences = [s for s in _SENTENCE_END.split(line.strip()) if s]
        kept = [s for s in sentences if not any(phrase in s for phrase in UNKNOWN_PHRASES)]
        if kept:
            kept_lines.append(" ".join(kept))
    return "\n".join(kept_lines).strip()


def _replace_reask_sentences(answer: str) -> str:
    """
    "문의를 남겨 주세요" 같은 문장을 지우고, 지운 것이 있으면 끝에 맺음말 한 줄을 붙입니다.
    지우고 나서 남는 내용이 없으면 빈 문자열(= AI 답변을 달지 않음)을 돌려줍니다.
    """
    removed = False
    kept_lines = []
    for line in answer.splitlines():
        sentences = [s for s in _SENTENCE_END.split(line.strip()) if s]
        kept = [s for s in sentences if not REASK_PATTERN.search(s)]
        removed = removed or len(kept) != len(sentences)
        if kept:
            kept_lines.append(" ".join(kept))
    body = "\n".join(kept_lines).strip()
    if not removed:
        return body
    return f"{body}\n{REASK_CLOSING}" if body else ""


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

    # 3. 챗봇과 같은 프롬프트 + 게시판 답변 규칙(답할 수 있는 부분만)으로 답변 생성
    result = structured_qa_llm.invoke(
        [SystemMessage(content=build_system_prompt(context) + QA_RULES), HumanMessage(content=message)]
    )
    answer = scrub_internal(strip_urls(result.answer or "")).strip()
    answer = _drop_unknown_sentences(answer)  # 못 찾은 부분에 대한 안내 문장은 지움 (답한 부분만 남김)
    answer = _replace_reask_sentences(answer)  # 이미 접수된 문의에 "문의를 남겨 달라"는 문장은 맺음말로 바꿈

    answered = (
        result.fromManual
        and not result.needsAdmin
        and len(answer) >= MIN_ANSWER_LENGTH
        and answer != FALLBACK_ANSWER
    )
    print(f"[QA 자동답변] answered={answered} fromManual={result.fromManual} needsAdmin={result.needsAdmin}")
    return {"answered": answered, "answer": answer} if answered else NOT_ANSWERED
