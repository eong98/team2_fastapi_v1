import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm
from shopsurvey.schema import ShopSurveySummaryRequest


llm = get_llm()

  
SYSTEM_PROMPT = (
    "당신은 매장 고객 설문 결과를 분석하는 AI입니다. "
    "응답 전체를 읽고 요약과 긍정/부정 점수를 함께 판단합니다. "
    "반드시 요청된 JSON 형식으로만 응답하세요."
)


def summarize_shop_survey(req: ShopSurveySummaryRequest) -> dict:
    """
    매장 설문 응답을 한 번의 LLM 호출로 분석한다.

    - summary : 점주가 바로 읽을 수 있는 전체 요약
    - score   : 0(매우 부정) ~ 5(중립) ~ 10(매우 긍정)
    - reason  : 점수 판단 근거

    요약과 점수를 같은 호출에서 받는 이유
    - 같은 데이터를 읽고 내린 판단이라 요약 내용과 점수가 서로 어긋나지 않음
    - H200 Gemma를 여러 팀이 공유하므로 추론 1회로 대기/처리 시간을 절반으로 줄임
    """

    if req.totalResponses <= 0:
        raise ValueError("요약할 응답이 없습니다.")

    data = req.model_dump(exclude_none=True)

    prompt = f"""
아래는 "{req.shopTitle or '매장'}" 매장의 고객 설문 "{req.surveyTitle}" 결과입니다.
총 응답 수는 {req.totalResponses}건입니다.

데이터 설명
- questions: 문항별 집계
  - SCALE 문항: scaleAvg는 0~scaleMax 점수의 평균 (높을수록 만족)
  - SINGLE/MULTI 문항: options에 보기별 선택 수
- textAnswers: 고객이 직접 쓴 서술형 답변 (최신순 일부)

작성할 내용

1. summary
- 점주가 읽을 요약, 한국어 3~5문장
- 고객이 만족한 점, 불만/개선 요청, 반복해서 나온 의견을 구체적으로
- 숫자가 의미 있으면 포함 (예: 청결 평균 8.2점, '재고 부족' 선택 12명)
- 데이터에 없는 내용은 추측하지 말 것

2. score
- 전체 응답의 긍정/부정 정도를 0~10 사이 숫자로 (소수 첫째 자리까지)
- 기준: 0 = 매우 부정, 3 = 대체로 부정, 5 = 중립 또는 긍정·부정이 비슷함,
        7 = 대체로 긍정, 10 = 매우 긍정
- SCALE 평균, 객관식 선택 분포, 서술형 답변의 감정을 함께 고려
- 응답 수가 적으면 극단값(0, 10)은 피할 것

3. reason
- score를 그렇게 판단한 근거, 한국어 1~2문장

반드시 아래 JSON 형식만 반환하세요.
{{
  "summary": "요약",
  "score": 7.5,
  "reason": "판단 근거"
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
        "summary": str(result["summary"]).strip(),
        "score": score,
        "reason": str(result["reason"]).strip(),
    }


def _remove_code_block(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()