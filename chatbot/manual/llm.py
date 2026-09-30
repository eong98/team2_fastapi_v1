"""
옵션메뉴 자동생성 — 전용 LLM, 구조화 출력(JSON) 안전장치, LLM 응답 스키마.
"""

import os
import re

from core.llm_client import get_llm
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field


def _build_menu_llm():
    """
    옵션생성 전용 LLM. 기본은 get_llm()과 같은 모델이며, 환경변수로 바꿀 수 있다.
      MENU_LLM_BASE_URL : 다른 Ollama 서버 사용 (예: SSH 터널로 연결한 H200 → http://localhost:11435)
      MENU_LLM_MODEL    : 다른 모델 사용 (예: 더 작은 모델로 CPU 속도 확보)
    num_predict로 출력 길이를 제한해 CPU에서 끝없이 길게 생성하는 것을 막고,
    keep_alive로 생성 도중 모델이 내려갔다 다시 올라오는 지연을 없앤다.
    reasoning=False: H200의 gemma4:26b처럼 "생각(thinking)" 기능이 있는 모델은 답하기 전에
    생각 텍스트를 먼저 만드는데, 이게 num_predict 한도를 다 써버려 실제 답(JSON)이 빈 문자열로
    오는 문제가 있었다(Invalid json output). 생각을 끄면 한도 안에서 바로 JSON을 만든다.
    생각 기능이 없는 모델(로컬 gemma2:9b)에는 영향 없음.
    """
    base = get_llm()
    return ChatOllama(
        model=os.getenv("MENU_LLM_MODEL") or base.model,
        base_url=os.getenv("MENU_LLM_BASE_URL") or base.base_url,
        temperature=0,
        num_ctx=4096,
        num_predict=1200,
        keep_alive="30m",
        reasoning=False,
    )


menu_llm = _build_menu_llm()


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> str:
    """응답 원문에서 JSON 부분만 꺼냄 — 생각(<think>) 블록, ```json 코드블록, 앞뒤 설명 제거."""
    text = _THINK_RE.sub("", text or "").strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else ""


def _invoke_structured(schema: type[BaseModel], runnable, messages: list):
    """
    구조화 출력 호출. 모델이 JSON을 설명·코드블록과 섞어 보내도 JSON만 꺼내 다시 읽고,
    끝내 실패하면 원인(응답 비었음/잘림/형식 오류)을 알 수 있는 메시지로 예외를 낸다.
    """
    out = runnable.invoke(messages)
    if out.get("parsed") is not None and out.get("parsing_error") is None:
        return out["parsed"]

    raw = out.get("raw")
    content = getattr(raw, "content", "") or ""
    done_reason = (getattr(raw, "response_metadata", None) or {}).get("done_reason")
    candidate = _extract_json(content)
    if candidate:
        try:
            return schema.model_validate_json(candidate)
        except Exception:
            pass

    print(f"⚠ AI 구조화 응답 해석 실패 ({schema.__name__}, done_reason={done_reason}): {content[:200]!r}")
    if not content.strip() or done_reason == "length":
        raise ValueError("AI 응답이 길이 제한에 걸려 비어 있거나 잘렸습니다. 다시 시도해주세요.")
    raise ValueError("AI 응답을 해석하지 못했습니다(JSON 형식 오류). 다시 시도해주세요.")


class _StructuredLLM:
    """menu_llm.with_structured_output 대신 사용 — 호출하는 쪽 코드는 그대로 .invoke(messages)."""

    def __init__(self, schema: type[BaseModel]):
        self.schema = schema
        self.runnable = menu_llm.with_structured_output(schema, include_raw=True)

    def invoke(self, messages: list):
        return _invoke_structured(self.schema, self.runnable, messages)


class TopicItem(BaseModel):
    label: str = Field(description="최상위(STEP1) 메뉴로 쓸 주제명. 15자 이내의 짧은 명사형")
    query: str = Field(description="이 주제의 내용을 문서에서 검색할 때 쓸 검색 문장")
    chunks: list[int] = Field(default_factory=list, description="이 주제 내용이 들어있는 청크 번호 목록")


class TopicPlan(BaseModel):
    topics: list[TopicItem] = Field(description="문서에 들어있는 서로 다른 주제 목록")


class MenuLeaf(BaseModel):
    label: str = Field(description="STEP3 선택지에 표시될 짧은 질문형 텍스트")
    answer: str = Field(description="이 선택지를 클릭했을 때 보여줄 답변(문서 근거, URL/경로 금지)")


class MenuMid(BaseModel):
    label: str = Field(description="STEP2 선택지에 표시될 짧은 텍스트")
    answer: str | None = Field(
        default=None,
        description=(
            "leaves가 비어 있으면 반드시 문서 근거로 사용자가 이해하기 쉬운 답변을 작성하세요. "
            "leaves가 있으면 null로 두세요. URL이나 /user/qa 같은 경로는 절대 쓰지 마세요."
        ),
    )
    leaves: list[MenuLeaf] = Field(
        default_factory=list,
        description="STEP3 하위 선택지 (0개 또는 2~5개). 1개뿐이거나 STEP2와 같은 내용이면 만들지 마세요.",
    )


class MenuTop(BaseModel):
    label: str = Field(description="최상위(STEP1) 카테고리명")
    children: list[MenuMid] = Field(description="STEP2 하위 선택지 목록 (최대 5개)")


class ShortLabels(BaseModel):
    labels: list[str] = Field(description="입력 순서 그대로, 각 제목을 요약한 결과")


class ItemPick(BaseModel):
    picks: list[int] = Field(description="사용자에게 가장 중요한 질문 번호 (최대 5개)")


class SectionCategories(BaseModel):
    picks: list[int] = Field(description="섹션 순서대로, 각 섹션이 들어갈 카테고리 번호 (맞는 곳이 없으면 -1)")


class CategoryName(BaseModel):
    label: str = Field(description="최상위 메뉴 이름. 10자 이내의 온전한 명사 (예: 고객센터, 문의사항, 매장 관리)")
    desc: str = Field(description="이 메뉴에 들어갈 내용 한 줄 설명 (사용자에게 보여줄 안내 문구로도 씀)")


class CategoryNames(BaseModel):
    categories: list[CategoryName] = Field(description="추천하는 최상위 메뉴 목록")


structured_topic_llm = _StructuredLLM(TopicPlan)


structured_top_llm = _StructuredLLM(MenuTop)


structured_pick_llm = _StructuredLLM(ItemPick)


structured_label_llm = _StructuredLLM(ShortLabels)


structured_classify_llm = _StructuredLLM(SectionCategories)


structured_catname_llm = _StructuredLLM(CategoryNames)
