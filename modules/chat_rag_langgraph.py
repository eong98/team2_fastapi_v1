"""
modules/chat_rag.py (LangGraph Version)

LangGraph 기반의 RAG 대화 그래프입니다.

흐름 구조:
 1. check_greeting_node: 단순 첫 인사어 패턴 정규식 검사 (history 없고 메시지
    전체가 정규식과 정확히 일치할 때만 인사로 판정, LLM 호출 없음)
    - 단순 인사일 경우 → greeting_node로 바로 이동
    - 아닐 경우 → search_node로 이동
 2. search_node: RAG 매뉴얼 선검색 및 유사도 콘솔 출력
 3. generate_node: 통합 LLM을 단 1회 호출하여 단순인사/일상대화 제한/
    매뉴얼 기반 요약/폴백(관리자 연결) 판단과 답변을 함께 생성

바깥에서 호출하는 answer_question(message, history) 시그니처와 반환 형태
({"answer": str, "needsAdmin": bool})는 원본(순수 함수) 버전과 동일하게
유지되어, chatbot/service.py는 수정할 필요가 없습니다.

LangGraph 작성 관례는 실습자료(agent_translator.py, agent_class_node.ipynb)를
따릅니다 — State는 TypedDict, 그래프 빌더 변수명은 graph_builder, 엔트리는
add_edge(START, ...), 조건부 엣지는 {"라벨": "노드이름"} 매핑 딕셔너리.
ToolNode/bind_tools는 쓰지 않습니다 — 이 그래프는 "검색을 항상 한다"는
구조라 AI가 도구 호출 여부를 스스로 판단할 필요가 없기 때문입니다.
"""

import re
from typing_extensions import TypedDict

from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm
from modules.manual_retriever import search_manual


llm = get_llm()


class ChatAnswer(BaseModel):
    """LLM 구조화 출력 스키마 (인사/일상대화/서비스질문 응답 공용)."""

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
    re.IGNORECASE,
)


### ==========================================
### 그래프 상태(State) 및 빌더 정의
### ==========================================

class State(TypedDict):
    message: str
    history: list
    route: str            # "greeting" | "question"
    docs_context: str     # search_node가 채움
    answer: str
    needsAdmin: bool


graph_builder = StateGraph(State)


### ==========================================
### 공통 유틸
### ==========================================

def _ensure_valid_answer(answer: str, fallback: str, retry_fn=None) -> str:
    """답변이 너무 짧거나 비어있을 때 폴백 처리."""
    if len(answer.strip()) >= MIN_ANSWER_LENGTH:
        return answer

    if retry_fn is not None:
        retried = retry_fn()
        if len(retried.strip()) >= MIN_ANSWER_LENGTH:
            return retried

    return fallback


def generate_greeting() -> str:
    """AI 상담 시작 시점의 인사말. 그래프와 별개(검색/분류 불필요, 즉시 LLM 호출)."""

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


### ==========================================
### 그래프 노드 정의
### ==========================================

# 인사 여부 판별 노드 (translator_node와 동일한 패턴 — 메시지를 보고
# 판단한 결과를 state에 실어 다음 노드로 넘김)
def check_greeting_node(state: State):
    """
    명확한 첫인사만 정규식으로 즉시 판별합니다.
    이 노드 자체는 LLM을 호출하지 않고 정규식만으로 즉시 판별합니다.
    조건: 이번이 대화 구간의 첫 메시지(history 없음)이면서, 메시지 전체가
    SIMPLE_GREETING_PATTERN과 정확히 일치할 때만 "greeting"으로 분류합니다.
    """
    message = state["message"]
    history = state.get("history") or []

    if not history and SIMPLE_GREETING_PATTERN.match(message.strip()):
        print(f"\n👉 [인사판별 노드] '{message}' → greeting")
        return {"route": "greeting"}

    print(f"\n👉 [인사판별 노드] '{message}' → question")
    return {"route": "question"}


graph_builder.add_node("check_greeting", check_greeting_node)


# 인사 응대 노드
def greeting_node(state: State):
    """단순 인사말(정규식으로 걸러진 것)에 검색 없이 바로 응대합니다."""
    message = state["message"]

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


graph_builder.add_node("greeting", greeting_node)


# 매뉴얼 검색 노드
def search_node(state: State):
    """ChromaDB에서 매뉴얼을 검색하고, 관련성 높은 문서만 골라 컨텍스트 문자열로 만듭니다."""
    message = state["message"]

    docs = search_manual(message, k=8)
    relevant_docs = [d for d in docs if d.get("score", 0.0) >= RELEVANCE_THRESHOLD]

    # 디버깅용 콘솔 로그 (검색 점수 확인용)
    print("\n" + "=" * 50)
    print(f"[RAG 검색 디버깅] 입력 메시지: '{message}'")
    for idx, d in enumerate(docs, 1):
        score = d.get("score", 0.0)
        passed = "O" if score >= RELEVANCE_THRESHOLD else "X"
        content_snippet = d.get("content", "")[:35].replace("\n", " ")
        print(f"  [{idx}] 점수: {score:.4f} [{passed}] | {content_snippet}...")
    print("=" * 50 + "\n")

    context = "\n\n".join(d["content"] for d in relevant_docs) if relevant_docs else "없음"
    return {"docs_context": context}


graph_builder.add_node("search", search_node)


# 답변 생성 노드
def generate_node(state: State):
    """
    통합 프롬프트로 1회 LLM 호출해서 답변+needsAdmin을 생성합니다.
    (단순인사/일상대화-잡담/서비스질문+자료있음/서비스질문+자료없음 4단계 지침 포함)
    check_greeting_node에서 안 걸러진 인사 변형이나 잡담은 여기서 1번/2번 지침으로 처리됩니다.
    """
    message = state["message"]
    history = state.get("history") or []
    context = state.get("docs_context", "없음")

    print(f"[멀티턴 디버깅] history 길이: {len(history)}개")
    for h in history:
        print(f"  - {type(h).__name__}: {h.content[:50]}")

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


graph_builder.add_node("generate", generate_node)


### ==========================================
### 조건부 라우팅 및 엣지 설정
### ==========================================

def route_after_greeting_check(state: State):
    """check_greeting_node의 route 값을 그대로 반환하는 라우터 함수."""
    return state["route"]


graph_builder.add_conditional_edges(
    "check_greeting",
    route_after_greeting_check,
    {"greeting": "greeting", "question": "search"},
)

graph_builder.add_edge(START, "check_greeting")
graph_builder.add_edge("greeting", END)
graph_builder.add_edge("search", "generate")
graph_builder.add_edge("generate", END)

graph = graph_builder.compile()


### ==========================================
### 외부 호출 인터페이스 (기존 시그니처 그대로 유지 — service.py 수정 불필요)
### ==========================================

def answer_question(message: str, history: list = None) -> dict:
    """
    사용자 질문에 대해 그래프(check_greeting→greeting 또는 check_greeting→search→generate)를
    실행해서 답변을 생성합니다.

    history: 이번 AI상담 구간에서 지금까지 주고받은 HumanMessage/AIMessage
             리스트 (맥락 유지용). None/빈 리스트면 이번이 첫 메시지로 취급합니다.

    반환값: { "answer": str, "needsAdmin": bool }
    """
    result = graph.invoke({"message": message, "history": history or []})
    return {"answer": result["answer"], "needsAdmin": result["needsAdmin"]}


def answer_greeting(message: str) -> dict:
    """
    하위 호환용 — 그래프의 greeting_node를 직접 실행합니다.
    """
    return greeting_node({"message": message})


if __name__ == "__main__":
    # 단독 테스트용 — python -m modules.chat_rag
    test_questions = [
        "안녕하세요",
        "고마워요",
        "너 이름이 뭐야",
        "구독권 취소하려면 어떻게 해야 하나요?",
        "로그인은 어떻게 해요?",
        "CCTV 화면이 안 나와요",
        "오늘 날씨 어때?",
    ]
    for q in test_questions:
        print("=" * 70)
        print(f"[질문] {q}")
        result = answer_question(q)
        print(f"  answer={result['answer']}")
        print(f"  needsAdmin={result['needsAdmin']}")

    # 멀티턴 시나리오 테스트
    print("\n" + "#" * 70)
    print("# 멀티턴 시나리오 테스트")
    print("#" * 70)
    from langchain_core.messages import AIMessage

    history = []
    turns = [
        "구독권 뭐 사야될까 고민돼",
        "CCTV 2개인 매장을 운영하고 싶어",
    ]
    for msg in turns:
        print("=" * 70)
        print(f"[질문] {msg}")
        result = answer_question(msg, history)
        print(f"  answer={result['answer']}")
        history.append(HumanMessage(content=msg))
        history.append(AIMessage(content=result["answer"]))