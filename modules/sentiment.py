import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


def analyze_sentiment(data) -> dict:
    """
    각 응답을 긍정/중립/부정으로 분류하고
    Python에서 실제 비율을 계산한다.
    """

    if not data:
        raise ValueError("감정 분석할 데이터가 없습니다.")

    positive_count = 0
    neutral_count = 0
    negative_count = 0

    for item in data:
        sentiment = _classify_sentiment(item)

        if sentiment == "POSITIVE":
            positive_count += 1

        elif sentiment == "NEUTRAL":
            neutral_count += 1

        elif sentiment == "NEGATIVE":
            negative_count += 1

        else:
            raise ValueError(
                f"알 수 없는 감정 분류 결과입니다: {sentiment}"
            )

    total_count = (
        positive_count
        + neutral_count
        + negative_count
    )

    if total_count == 0:
        raise ValueError("감정 분석 결과가 없습니다.")

    return {
        "positiveRate": round(
            positive_count / total_count * 100, 2
        ),
        "neutralRate": round(
            neutral_count / total_count * 100, 2
        ),
        "negativeRate": round(
            negative_count / total_count * 100, 2
        ),
        "positiveCount": positive_count,
        "neutralCount": neutral_count,
        "negativeCount": negative_count,
        "totalCount": total_count,
    }


def _classify_sentiment(item) -> str:

    prompt = f"""
당신은 사용자 응답의 감정을 분류하는 AI입니다.

아래 하나의 질문과 답변을 분석하여
POSITIVE, NEUTRAL, NEGATIVE 중 하나로 분류하세요.

질문과 답변의 의미를 함께 고려하고,
SCORE 유형이라면 점수의 의미도 고려하세요.

반드시 아래 JSON 형식만 반환하세요.

{{"sentiment": "POSITIVE"}}

JSON 외의 설명이나 마크다운은 출력하지 마세요.

분석 데이터:
{json.dumps(item, ensure_ascii=False, indent=2)}
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "사용자 응답을 긍정, 중립, 부정으로 "
                    "분류하고 반드시 JSON으로만 응답하세요."
                )
            ),
            HumanMessage(content=prompt)
        ]
    )

    content = _remove_code_block(
        response.content.strip()
    )

    result = json.loads(content)

    if "sentiment" not in result:
        raise ValueError(
            "AI 응답에 sentiment 값이 없습니다."
        )

    sentiment = str(
        result["sentiment"]
    ).strip().upper()

    if sentiment not in {
        "POSITIVE",
        "NEUTRAL",
        "NEGATIVE"
    }:
        raise ValueError(
            "감정 분석 결과는 "
            "POSITIVE, NEUTRAL, NEGATIVE 중 하나여야 합니다."
        )

    return sentiment


def _remove_code_block(content: str) -> str:

    if content.startswith("```json"):
        content = content[7:]

    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()