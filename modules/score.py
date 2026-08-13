import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


def analyze_score(data) -> float:
    """
    입력 데이터를 종합 분석하여
    1.0~5.0 사이 AI 종합평가점수를 반환한다.
    """

    prompt = f"""
당신은 사용자 응답 데이터를 종합적으로 평가하는 AI입니다.

아래 입력 데이터에는 점수형, 선택형, 주관식 등
다양한 형식이 포함될 수 있습니다.

전체 데이터를 종합적으로 분석하여
1.0부터 5.0 사이의 AI 종합평가점수를 산출하세요.

중요:
- 숫자형 응답의 단순 산술평균만 계산하지 마세요.
- 선택형 응답의 경향도 고려하세요.
- 주관식 답변의 긍정·부정 내용도 고려하세요.
- 전체 데이터의 맥락을 함께 판단하세요.

평가 기준:
1.0 = 매우 부정적
2.0 = 부정적
3.0 = 중립적 또는 보통
4.0 = 긍정적
5.0 = 매우 긍정적

반드시 아래 JSON 형식만 반환하세요.

{{"score": 1.0}}

JSON 외의 설명이나 마크다운은 출력하지 마세요.

분석 데이터:
{json.dumps(data, ensure_ascii=False, indent=2)}
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "당신은 데이터를 종합 평가하는 AI입니다. "
                    "반드시 요청된 JSON 형식으로만 응답하세요."
                )
            ),
            HumanMessage(content=prompt)
        ]
    )

    content = _remove_code_block(
        response.content.strip()
    )

    result = json.loads(content)

    if "score" not in result:
        raise ValueError(
            "AI 응답에 score 값이 없습니다."
        )

    score = float(result["score"])

    if score < 1.0 or score > 5.0:
        raise ValueError(
            "AI 평가점수는 1.0부터 5.0 사이여야 합니다."
        )

    return round(score, 1)


def _remove_code_block(content: str) -> str:

    if content.startswith("```json"):
        content = content[7:]

    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()