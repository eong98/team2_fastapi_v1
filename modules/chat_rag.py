"""
modules/chat_rag.py

사용자의 자유텍스트 질문을 받아, 매뉴얼 검색(RAG) 기반으로 답변을 생성합니다.

개선 사항:
- 단순 인사(첫 인사 등)를 제외한 일상 대화, 고민 상담, 날씨 등 무관한 주제는 
  서비스 관련 질문만 부탁드린다는 안내 문구 출력 (needsAdmin=False)
- 단 1회의 LLM 호출로 속도 최적화 및 5줄 이하 답변 제약 유지
"""

import re
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm
from modules.manual_retriever import search_manual


llm = get_llm()


class ChatAnswer(BaseModel):
    """단일 LLM 응답을 위한 통합 구조화 스키마"""

    answer: str = Field(description="사용자에게 전달할 자연스러운 한국어 답변 (최대 5줄 이하)")
    needsAdmin: bool = Field(
        description=(
            "사용자가 서비스 관련 문의/질문을 했으나 매뉴얼 정보가 전혀 없어 관리자 확인이 필요한 경우에만 true. "
            "첫 인사말, 서비스 유관 질문, 일상대화/고민상담 안내의 경우 false"
        )
    )


structured_answer_llm = llm.with_structured_output(ChatAnswer)

MIN_ANSWER_LENGTH = 5
FALLBACK_ANSWER = "죄송합니다, 정확한 답변을 찾지 못했습니다."
GREETING_FALLBACK = "안녕하세요! 저는 Allimio의 AI 상담봇 알리미입니다. 무엇을 도와드릴까요?"

# RAG 문턱
RELEVANCE_THRESHOLD = 0.3

# 단순 첫 인사어 정규식 (LLM 및 DB 호출 절약용)
SIMPLE_GREETING_PATTERN = re.compile(
    r"^(안녕|안녕하세요|반가워|반갑습니다|하이|hi|hello|수고하세요|수고하십시오)[\s\!\?\.]*$",
    re.IGNORECASE
)


def _ensure_valid_answer(answer: str, fallback: str, retry_fn=None) -> str:
    """답변이 너무 짧거나 비어있을 때 폴백 처리"""
    if len(answer.strip()) >= MIN_ANSWER_LENGTH:
        return answer

    if retry_fn is not None:
        retried = retry_fn()
        if len(retried.strip()) >= MIN_ANSWER_LENGTH:
            return retried

    return fallback


def generate_greeting() -> str:
    """AI 상담 시작 시점의 인사말"""

    def _generate():
        result = structured_answer_llm.invoke(
            [
                SystemMessage(
                    content=(
                        "당신은 Allimio 서비스의 AI 상담봇 '알리미'입니다. "
                        "방금 AI 상담이 시작되었습니다. 사용자에게 짧고 친근하게 인사하고, "
                        "무엇을 도와드릴지 물어보세요. "
                        "반드시 2줄 이하로 짧게, 개발 용어 없이 자연스러운 대화체로 답하세요."
                    )
                ),
                HumanMessage(content="AI 상담 시작"),
            ]
        )
        return result.answer

    return _ensure_valid_answer(_generate(), fallback=GREETING_FALLBACK, retry_fn=_generate)


def answer_greeting(message: str) -> dict:
    """단순 첫 인사말 즉시 응대"""

    def _generate():
        result = structured_answer_llm.invoke(
            [
                SystemMessage(
                    content=(
                        "당신은 Allimio 서비스의 AI 상담봇 '알리미'입니다. "
                        "사용자가 인사를 건넸습니다. "
                        "존댓말로 친절하고 상냥하게 인사를 받아주고 무엇을 도와드릴지 2줄 이하로 물어보세요."
                    )
                ),
                HumanMessage(content=message),
            ]
        )
        return result.answer

    answer = _ensure_valid_answer(_generate(), fallback=GREETING_FALLBACK, retry_fn=_generate)
    return {"answer": answer, "needsAdmin": False}


def answer_question(message: str, history: list = None) -> dict:
    """매뉴얼 검색 + 통합 LLM 판단 (서비스 무관 일상대화 제한 기능 추가)"""
    history = history or []

    # 1) 명확한 첫 인사어는 즉시 응대
    if not history and SIMPLE_GREETING_PATTERN.match(message.strip()):
        return answer_greeting(message)

    # 2) RAG 검색
    docs = search_manual(message, k=8)
    relevant_docs = [d for d in docs if d.get("score", 0.0) >= RELEVANCE_THRESHOLD]

    # 디버깅용 콘솔 로그
    print("\n" + "=" * 50)
    print(f"[RAG 검색 디버깅] 입력 메시지: '{message}'")
    for idx, d in enumerate(docs, 1):
        score = d.get("score", 0.0)
        passed = "O" if score >= RELEVANCE_THRESHOLD else "X"
        content_snippet = d.get("content", "")[:35].replace("\n", " ")
        print(f"  [{idx}] 점수: {score:.4f} [{passed}] | {content_snippet}...")
    print("=" * 50 + "\n")

    context = "\n\n".join(d["content"] for d in relevant_docs) if relevant_docs else "없음"

    # 3) 통합 프롬프트: 일상대화/고민상담/날씨 제한 지침 포함
    system_prompt = f"""
당신은 Allimio 서비스의 AI 상담원 '알리미'입니다.
사용자의 메시지와 아래 [참고 자료]를 종합적으로 판단하여 적절히 답변하세요.

[답변 핵심 제약 조건]
- 모든 답변은 **최대 5줄 이하**로만 작성하세요.
- 불필요한 서론을 빼고 핵심만 요약하세요.

[답변 처리 지침]
1. **단순 인사말 (예: "안녕하세요", "반갑습니다")**:
   - 친절하고 상냥하게 인사를 나누고 무엇을 도와드릴지 안내하세요.
   - `needsAdmin`: false

2. **일상 대화 / 고민 상담 / 날씨 / 심심풀이 잡담 / 서비스와 전혀 무관한 질문인 경우**:
   - 일상 대화나 개인적인 질문에는 답변하기 어렵다는 점을 친절히 양해 구하고, **Allimio 서비스와 관련된 내용에 대해서만 질문해 달라고 안내**하세요.
   - 예시: "죄송합니다, 저는 Allimio 서비스 상담을 돕는 AI 알리미입니다. 일상 대화나 날씨 질문에는 답변해 드리기 어려우니, Allimio 서비스 이용이나 요금제 등 관련 문의를 부탁드립니다!"
   - `needsAdmin`: false

3. **Allimio 서비스 관련 문의이며 [참고 자료]에 연관된 내용(구독권, 요금 등)이 일부라도 있는 경우**:
   - 참고 자료를 기반으로 핵심 내용을 요약/재구성하여 5줄 이하로 알맞게 설명하세요.
   - `needsAdmin`: false

4. **Allimio 서비스 관련 문의지만 [참고 자료]에 대략적인 정보조차 전혀 없는 경우**:
   - "죄송합니다, 요청하신 정확한 정보를 찾지 못했습니다. 담당자(관리자)에게 연결해 드릴까요?" 취지로 짧게 안내하세요.
   - `needsAdmin`: true

[주의사항]
- "참고 자료", "context", "문서" 등 시스템/개발 용어는 절대 포함하지 마세요.
- 대화 이력이 있다면 이전 대화 맥락에 맞춰 자연스럽게 이어가세요.

[참고 자료]
{context}
"""

    result = structured_answer_llm.invoke(
        [SystemMessage(content=system_prompt), *history, HumanMessage(content=message)]
    )

    return {"answer": result.answer, "needsAdmin": result.needsAdmin}