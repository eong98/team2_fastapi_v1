# -*- coding: utf-8 -*-
"""
cctv/agent/review.py

CCTV 이슈 AI 검토 에이전트.

이슈 1건에 대해 "정탐일 가능성이 높은지 / 오탐일 가능성이 높은지"를 관련 기록을 조회해 가며
판단하고, 근거와 권장 조치를 돌려준다. 정탐/오탐을 확정하지는 않는다(확정은 화면의 버튼으로 사람이 한다).

[기존 LLM 모듈과 다른 점 - 왜 '에이전트'인가]
modules/cctv_issue.py 등은 프롬프트 1번 -> JSON 1번으로 끝난다(무엇을 볼지 코드가 정함).
여기서는 LLM이 매 차례 "다음에 어떤 도구로 무엇을 조회할지"를 스스로 고르고,
그 결과를 본 뒤 더 조회할지 결론을 낼지 다시 정한다. 유형마다 봐야 할 것이 달라서다.
  - 무단침입: 당일 매장 일정(점검/공사)이 있었나
  - 기물파손: 그 시각 매장에 사람이 있었나
  - 폭행    : 이 카메라에서 폭행이 자주 오탐이었나, 직전에 같은 감지가 반복됐나

[흐름 - LangGraph]
    START -> decide -(도구실행)-> act -> decide -> ... -(종료)-> END
                    -(재시도)-> decide
                    -(대체)-> fallback -> END

[도구 호출을 bind_tools가 아니라 JSON으로 고르게 한 이유]
core/llm_client.py의 LLM은 format="json"으로 고정돼 있고, 로컬 폴백 모델(gemma2:9b)은
Ollama의 tool calling을 지원하지 않는다. 그래서 LLM이 {"action": "도구이름", "args": {...}}
JSON을 내면 이 코드가 해당 도구를 실행하는 방식을 썼다. 두 모델에서 똑같이 동작하고
공용 파일(core/llm_client.py)을 고칠 필요가 없다.

[안전장치]
- 도구 호출은 최대 MAX_TOOL_CALLS번, LLM 호출은 최대 MAX_TURNS번 (무한 루프 방지)
- 같은 도구를 같은 인자로 다시 부르면 실행하지 않고 다른 선택을 요구
- LLM이 죽거나 형식이 계속 틀리면 '판단 보류'로 끝내되, 조회한 기록은 그대로 보여준다
"""

import json
import time
from typing import List, Optional

from typing_extensions import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from core.llm_client import get_llm
from cctv.agent.tools import DEFAULT_TOOL_ORDER, TOOLS, IssueContext, load_issue

MAX_TOOL_CALLS = 4      # 도구 조회 횟수 상한
MAX_TURNS = 7           # LLM 호출 횟수 상한 (도구 4번 + 결론 1번 + 형식 오류 여유 2번)
MIN_TOOL_CALLS = 1      # 아무것도 조회하지 않고 결론 내는 것 방지

VERDICT_LABELS = {
    "LIKELY_TRUE": "정탐 가능성 높음",
    "LIKELY_FALSE": "오탐 가능성 높음",
    "UNCERTAIN": "판단 보류",
}

_SYSTEM_PROMPT = (
    "당신은 무인매장 CCTV 이상행동 이슈를 검토하는 보안 관제 보조 AI입니다. "
    "주어진 도구로 관련 기록을 조회한 뒤, 이 감지가 실제 상황(정탐)일지 잘못된 감지(오탐)일지 "
    "의견과 근거를 제시합니다. 최종 확정은 사람이 합니다. 반드시 JSON만 반환하세요."
)

_llm = None


def _get_llm():
    """LLM을 처음 쓸 때 한 번만 만든다.

    H200의 gemma4는 답하기 전에 '생각' 텍스트를 길게 만드는 모델이라, 여러 번 호출하는
    에이전트에서는 응답이 크게 느려진다. chat_rag_langgraph.py와 같은 방식으로 복사본에만
    생각 기능을 끈다(core/llm_client.py는 건드리지 않음).
    """
    global _llm
    if _llm is None:
        llm = get_llm()
        try:
            llm = llm.model_copy(update={"reasoning": False})
        except Exception:
            pass  # reasoning 옵션이 없는 버전이면 원본 그대로 사용
        _llm = llm
    return _llm


# ---------------------------------------------------------------------------
# 그래프 상태
# ---------------------------------------------------------------------------

class ReviewState(TypedDict):
    ctx: IssueContext           # 검토 대상 이슈 (서버가 고정)
    steps: List[dict]           # 실행한 도구와 결과 [{tool, label, args, summary, data}]
    turns: int                  # LLM을 호출한 횟수
    notes: List[str]            # LLM에게 돌려줄 교정 메시지 (형식 오류, 중복 호출 등)
    pending: Optional[dict]     # 다음에 실행할 도구 {tool, args}
    final: Optional[dict]       # 결론 {verdict, reasons, recommendation}
    failed: bool                # LLM 사용 불가 -> fallback으로 보냄


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

def _build_prompt(state: ReviewState) -> str:
    ctx, steps = state["ctx"], state["steps"]
    remaining = MAX_TOOL_CALLS - len(steps)

    tool_lines = "\n".join(
        f'- {t.name}: {t.description} 인자: {t.args}' for t in TOOLS.values()
    )

    if steps:
        observed = "\n".join(
            f'{i}. {s["tool"]}({json.dumps(s["args"], ensure_ascii=False)}) -> {s["summary"]}\n'
            f'   상세: {json.dumps(s["data"], ensure_ascii=False)}'
            for i, s in enumerate(steps, start=1)
        )
    else:
        observed = "(아직 조회한 기록이 없습니다)"

    notes = "\n".join(f"- {n}" for n in state["notes"][-2:])  # 최근 교정 메시지만

    if remaining <= 0:
        next_rule = "더 이상 조회할 수 없습니다. 지금까지의 조회 결과만으로 결론(finish)을 내세요."
    else:
        next_rule = (
            f"앞으로 최대 {remaining}번 더 조회할 수 있습니다. "
            "판단에 필요한 기록이 더 있으면 도구를 고르고, 충분하면 결론(finish)을 내세요."
        )

    return f"""
[검토할 이슈]
{json.dumps(ctx.describe(), ensure_ascii=False, indent=2)}

[사용할 수 있는 도구]
{tool_lines}

[지금까지 조회한 결과]
{observed}

[다음 행동]
{next_rule}
{("[주의]" + chr(10) + notes) if notes else ""}

[응답 형식 - 아래 둘 중 하나의 JSON만 반환]
1) 도구를 조회할 때
{{"thought": "왜 이 조회가 필요한지 한 문장", "action": "도구이름", "args": {{}}}}

2) 결론을 낼 때
{{"action": "finish",
  "verdict": "LIKELY_TRUE | LIKELY_FALSE | UNCERTAIN 중 하나",
  "reasons": ["조회 결과에 근거한 이유 (2~4개, 각 한 문장)"],
  "recommendation": "관리자가 지금 할 일 한 문장"}}

[판단 규칙]
- 이유에는 위 '조회한 결과'에 실제로 있는 사실만 쓰세요. 조회하지 않은 내용을 추측해서 쓰지 마세요.
- 같은 도구를 같은 인자로 다시 조회하지 마세요.
- 이 유형과 관련 있는 기록부터 조회하세요. 모든 도구를 다 쓸 필요는 없습니다.
- 심각도가 '높음'인 유형(폭행·쓰러짐·화재 등)은 오탐이라는 뚜렷한 근거가 없으면 LIKELY_FALSE로 판단하지 말고,
  애매하면 UNCERTAIN으로 두고 현장 확인을 권하세요. 실제 상황을 놓치는 쪽이 더 위험합니다.
- 방문객 기록이 0건이라는 것만으로 '사람이 없었다'고 단정하지 마세요(집계가 안 됐을 수 있음).
- JSON 외의 설명이나 마크다운은 출력하지 마세요.
"""


def _parse_json(content) -> dict:
    """LLM 응답에서 JSON 객체를 꺼낸다. 코드블록(```)으로 감싸 와도 처리한다."""
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    result = json.loads(text.strip())
    if not isinstance(result, dict):
        raise ValueError("JSON 객체가 아닙니다.")
    return result


def _clean_final(raw: dict) -> dict:
    """LLM이 낸 결론을 검증해서 화면에 내보낼 형태로 정리한다. 형식이 틀리면 ValueError."""
    verdict = str(raw.get("verdict", "")).strip().upper()
    if verdict not in VERDICT_LABELS:
        raise ValueError(f"verdict 값이 올바르지 않습니다: {verdict!r}")

    reasons = raw.get("reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(r).strip() for r in (reasons or []) if str(r).strip()][:4]
    if not reasons:
        raise ValueError("reasons가 비어 있습니다.")

    recommendation = str(raw.get("recommendation", "")).strip()
    if not recommendation:
        raise ValueError("recommendation이 비어 있습니다.")

    return {"verdict": verdict, "reasons": reasons, "recommendation": recommendation}


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------

def decide_node(state: ReviewState) -> dict:
    """LLM에게 다음 행동(도구 조회 또는 결론)을 고르게 한다."""
    turns = state["turns"] + 1
    notes = list(state["notes"])

    try:
        response = _get_llm().invoke(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=_build_prompt(state))]
        )
    except Exception as e:
        # Ollama가 꺼져 있는 등 LLM 자체를 못 쓰는 경우 - 다시 불러도 같으니 바로 대체 경로로
        print(f"[cctv_review] LLM 호출 실패: {e}")
        return {"turns": turns, "failed": True, "pending": None}

    try:
        raw = _parse_json(response.content)
        action = str(raw.get("action", "")).strip()

        if action == "finish":
            if len(state["steps"]) < MIN_TOOL_CALLS:
                raise ValueError("기록을 하나도 조회하지 않았습니다. 먼저 도구로 조회하세요.")
            return {"turns": turns, "final": _clean_final(raw), "pending": None}

        if action not in TOOLS:
            raise ValueError(f"없는 도구입니다: {action!r}. 도구 목록의 이름만 쓰세요.")

        if len(state["steps"]) >= MAX_TOOL_CALLS:
            raise ValueError("조회 횟수를 모두 썼습니다. finish로 결론을 내세요.")

        args = raw.get("args")
        args = args if isinstance(args, dict) else {}

        # 같은 도구 + 같은 인자 반복 방지 (약한 모델이 같은 조회만 되풀이하는 것을 막는다)
        signature = (action, json.dumps(args, sort_keys=True, ensure_ascii=False))
        done = {(s["tool"], json.dumps(s["args"], sort_keys=True, ensure_ascii=False)) for s in state["steps"]}
        if signature in done:
            raise ValueError(f"{action}은 이미 같은 조건으로 조회했습니다. 다른 도구를 쓰거나 finish 하세요.")

        return {"turns": turns, "pending": {"tool": action, "args": args}}

    except Exception as e:
        # 형식 오류는 LLM에게 알려주고 다시 고르게 한다
        print(f"[cctv_review] 응답 형식 오류({turns}번째): {e}")
        notes.append(str(e))
        return {"turns": turns, "notes": notes, "pending": None}


def act_node(state: ReviewState) -> dict:
    """LLM이 고른 도구를 실행하고 결과를 기록한다."""
    pending = state["pending"]
    tool = TOOLS[pending["tool"]]

    try:
        result = tool.run(state["ctx"], pending["args"])
    except Exception as e:
        # 테이블/컬럼 문제 등으로 조회가 실패해도 검토 전체가 죽지 않게 한다
        print(f"[cctv_review] 도구 실행 실패 ({tool.name}): {e}")
        result = {"summary": "조회 중 오류가 발생해 결과를 가져오지 못했습니다.", "data": {}}

    step = {
        "tool": tool.name,
        "label": tool.label,
        "args": pending["args"],
        "summary": result["summary"],
        "data": result.get("data", {}),
    }
    return {"steps": state["steps"] + [step], "pending": None}


def fallback_node(state: ReviewState) -> dict:
    """LLM으로 결론을 내지 못했을 때. 판단은 보류하되 조회한 기록은 보여준다."""
    steps = list(state["steps"])

    if not steps:
        # LLM이 한 번도 도구를 고르지 못한 경우 - 기본 조회라도 돌려서 근거를 제공
        for name in DEFAULT_TOOL_ORDER:
            tool = TOOLS[name]
            try:
                result = tool.run(state["ctx"], {})
            except Exception as e:
                print(f"[cctv_review] 기본 조회 실패 ({name}): {e}")
                continue
            steps.append({"tool": tool.name, "label": tool.label, "args": {},
                          "summary": result["summary"], "data": result.get("data", {})})

    return {
        "steps": steps,
        "final": {
            "verdict": "UNCERTAIN",
            "reasons": ["AI가 판단을 완료하지 못했습니다. 아래 조회 기록을 참고해 직접 확인해주세요."],
            "recommendation": "CCTV 영상과 현장을 직접 확인한 뒤 정탐/오탐을 처리해주세요.",
            "fallback": True,
        },
    }


def _route(state: ReviewState) -> str:
    """decide 다음에 어디로 갈지."""
    if state.get("final"):
        return "종료"
    if state.get("failed") or state["turns"] >= MAX_TURNS:
        return "대체"
    if state.get("pending"):
        return "도구실행"
    return "재시도"


# ---------------------------------------------------------------------------
# 그래프 구성
# ---------------------------------------------------------------------------

graph_builder = StateGraph(ReviewState)
graph_builder.add_node("decide", decide_node)
graph_builder.add_node("act", act_node)
graph_builder.add_node("fallback", fallback_node)

graph_builder.add_edge(START, "decide")
graph_builder.add_conditional_edges(
    "decide",
    _route,
    {"도구실행": "act", "재시도": "decide", "종료": END, "대체": "fallback"},
)
graph_builder.add_edge("act", "decide")      # 도구 결과를 들고 다시 판단하러 간다 (루프)
graph_builder.add_edge("fallback", END)

review_graph = graph_builder.compile()


# ---------------------------------------------------------------------------
# 바깥에서 부르는 함수
# ---------------------------------------------------------------------------

def review_issue(no: int, sno: Optional[int] = None) -> dict:
    """이슈 1건을 검토해 의견을 돌려준다.

    sno를 주면 그 이슈가 해당 매장의 CCTV에서 발생한 것인지 확인한다(다른 매장 이슈 조회 방지).
    - 이슈가 없으면 LookupError
    - 매장이 다르면 PermissionError
    """
    started = time.time()
    ctx = load_issue(no)

    if sno is not None and ctx.sno != sno:
        raise PermissionError("선택한 매장의 이슈가 아닙니다.")

    state = review_graph.invoke(
        {"ctx": ctx, "steps": [], "turns": 0, "notes": [],
         "pending": None, "final": None, "failed": False},
        # 노드 실행 횟수 상한. 정상 흐름은 decide/act 합쳐 MAX_TURNS + MAX_TOOL_CALLS + 1 이내
        config={"recursion_limit": 30},
    )

    final = state["final"]
    return {
        "no": ctx.no,
        "code": ctx.code,
        "codeName": ctx.code_name,
        "verdict": final["verdict"],
        "verdictLabel": VERDICT_LABELS[final["verdict"]],
        "reasons": final["reasons"],
        "recommendation": final["recommendation"],
        # 화면에는 도구 이름 대신 한글 라벨과 한 줄 요약만 보낸다(data는 프롬프트용)
        "steps": [{"tool": s["tool"], "label": s["label"], "summary": s["summary"]} for s in state["steps"]],
        "fallback": bool(final.get("fallback", False)),
        "elapsedMs": int((time.time() - started) * 1000),
    }
