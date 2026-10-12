"""
chatstats/service.py

챗봇 상담 만족도 AI 분석 (감정 점수 + 요약).

매장 고객 설문 AI 요약(shopsurvey/service.py)과 같은 방식입니다.
- 요약과 점수를 한 번의 LLM 호출로 받음 (같은 데이터를 읽고 내린 판단이라 서로 어긋나지 않고,
  여러 팀이 같이 쓰는 H200 모델의 추론 횟수를 줄임)
- 결과 형식도 같음: summary / score(0~10) / reason / weakPoints
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm
from chatstats.schema import ChatStatsSummaryRequest


# H200 gemma4:26b 같은 생각(thinking) 모델은 답 전에 생각 텍스트를 길게 만들어 느리고
# 출력 한도에 걸리면 답이 비어 JSON 파싱이 실패함 → 가져온 LLM의 복사본에만 생각 끄기
# (core/llm_client.py는 수정하지 않음, 생각 기능 없는 로컬 모델엔 영향 없음)
llm = get_llm().model_copy(update={"reasoning": False})


SYSTEM_PROMPT = (
    "당신은 고객센터 챗봇의 상담 만족도 조사 결과를 분석하는 AI입니다. "
    "집계 숫자와 고객 의견을 읽고 요약과 긍정/부정 점수를 함께 판단합니다. "
    "반드시 요청된 JSON 형식으로만 응답하세요."
)

# 요약 최대 단어 수 (띄어쓰기 기준, 이 값 미만으로 유지)
MAX_SUMMARY_WORDS = 100

# 개선할 점 최대 개수 / 항목당 최대 글자 수
MAX_WEAK_POINTS = 5
MAX_WEAK_POINT_LENGTH = 80


def summarize_chat_stats(req: ChatStatsSummaryRequest) -> dict:
    """
    챗봇 상담 만족도 조사 결과를 한 번의 LLM 호출로 분석한다.

    - summary    : 관리자가 바로 읽을 수 있는 전체 요약
    - score      : 0(매우 부정) ~ 5(중립) ~ 10(매우 긍정)
    - reason     : 점수 판단 근거
    - weakPoints : 챗봇 상담에서 개선할 점
    """

    if req.answered <= 0:
        raise ValueError("분석할 만족도 응답이 없습니다.")

    data = req.model_dump()

    prompt = f"""
아래는 Allimio 서비스 챗봇 상담의 만족도 조사 결과입니다. (기간: {req.period})
상담을 끝낼 때 고객이 "만족 / 불만족"을 고르고, 불만족이면 사유를 고르거나 직접 적습니다.

데이터 설명
- closed: 종료된 상담 수, answered: 만족도에 응답한 수
- satisfied / dissatisfied: 만족 / 불만족 응답 수
- adminConnect: 챗봇으로 해결되지 않아 관리자 문의로 넘어간 상담 수
- autoClosed: 고객이 답 없이 떠나 자동 종료된 상담 수 (만족도 미응답)
- reasons: 불만족 사유별 건수
- opinions: 불만족 고객이 직접 쓴 의견
- questions: 불만족 상담에서 고객이 고른 메뉴와 입력한 질문
  (화면 버튼 문구가 섞여 있을 수 있음 — 질문 내용만 참고)

작성할 내용

1. summary
- 관리자가 읽을 요약, 한국어 3~4문장
- 반드시 100단어 미만 (띄어쓰기 기준으로 센 단어 수)
- 만족·불만족 비율, 가장 많은 불만족 사유, 고객 의견과 질문에서 반복되는 내용을 구체적으로
- 숫자가 의미 있으면 포함 (예: 응답 8건 중 만족 5건, '원하는 답을 못 찾음' 2건)
- 데이터에 없는 내용은 추측하지 말 것

2. score
- 전체 응답의 긍정/부정 정도를 0~10 사이 숫자로 (소수 첫째 자리까지)
- 기준: 0 = 매우 부정, 3 = 대체로 부정, 5 = 중립 또는 긍정·부정이 비슷함,
        7 = 대체로 긍정, 10 = 매우 긍정
- 만족·불만족 비율을 중심으로, 직접 쓴 의견의 감정과 관리자 문의로 넘어간 비율을 함께 고려
- 응답 수가 적으면 극단값(0, 10)은 피할 것

3. reason
- score를 그렇게 판단한 근거, 한국어 1~2문장

4. weakPoints
- 챗봇 상담에서 개선할 점, 최대 5개
- 불만족 사유, 직접 쓴 의견, 불만족 상담의 질문에서 드러난 문제
- 항목마다 "무엇을 / 왜 개선해야 하는지"를 근거와 함께 한 줄로
  (예: "구독권 환불 안내 - 환불 규정 질문에 다른 내용이 안내됐다는 의견")
- 개선할 점이 없으면 빈 배열 []

반드시 아래 JSON 형식만 반환하세요.
{{
  "summary": "요약",
  "score": 7.5,
  "reason": "판단 근거",
  "weakPoints": ["개선할 점 1", "개선할 점 2"]
}}

분석 데이터:
{json.dumps(data, ensure_ascii=False, indent=2)}
"""

    response = llm.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
    )

    result = json.loads(_remove_code_block(response.content.strip()))

    for field in ("summary", "score", "reason"):
        if field not in result:
            raise ValueError(f"AI 응답에 {field} 값이 없습니다.")

    try:
        score = float(result["score"])
    except (TypeError, ValueError):
        raise ValueError("AI 응답의 score가 숫자가 아닙니다.")

    # 범위 보정 (0 ~ 10, 소수 첫째 자리)
    score = round(min(10.0, max(0.0, score)), 1)

    return {
        "summary": _limit_words(str(result["summary"]).strip(), MAX_SUMMARY_WORDS),
        "score": score,
        "reason": str(result["reason"]).strip(),
        "weakPoints": _clean_weak_points(result.get("weakPoints")),
    }


def _clean_weak_points(value) -> list[str]:
    """개선할 점 정리: 배열이 아니면 빈 배열, 빈 값 제거, 개수/길이 제한 (형식이 틀려도 전체를 실패시키지 않음)."""
    if not isinstance(value, list):
        return []

    points = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > MAX_WEAK_POINT_LENGTH:
            text = text[:MAX_WEAK_POINT_LENGTH] + "…"
        points.append(text)
        if len(points) >= MAX_WEAK_POINTS:
            break

    return points


def _limit_words(text: str, max_words: int) -> str:
    """띄어쓰기 기준 max_words 단어 미만으로 자른다. 가능하면 마지막 완성된 문장에서 끊는다."""
    words = text.split()
    if len(words) < max_words:
        return text

    cut = " ".join(words[: max_words - 1])

    last_end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if last_end > len(cut) // 2:  # 너무 짧아지지 않을 때만 문장 경계에서 자름
        return cut[: last_end + 1]

    return cut + "…"


def _remove_code_block(content: str) -> str:
    """LLM이 JSON을 코드블록으로 감싸서 준 경우 벗겨낸다."""
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()
