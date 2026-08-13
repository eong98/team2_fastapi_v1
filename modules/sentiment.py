import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


def analyze_sentiment(data) -> dict:
    """
    전체 응답을 한 번에 AI에 전달하여
    각 응답의 감정을 분류한 뒤
    Python에서 실제 개수와 비율을 계산한다.
    """

    if not data:
        raise ValueError("감정 분석할 데이터가 없습니다.")

    sentiments = _classify_sentiments(data)

    positive_count = sentiments.count("POSITIVE")
    neutral_count = sentiments.count("NEUTRAL")
    negative_count = sentiments.count("NEGATIVE")
    total_count = len(sentiments)

    if total_count == 0:
        raise ValueError("감정 분석 결과가 없습니다.")

    return {
        "positiveRate": round(positive_count / total_count * 100, 2),
        "neutralRate": round(neutral_count / total_count * 100, 2),
        "negativeRate": round(negative_count / total_count * 100, 2),
        "positiveCount": positive_count,
        "neutralCount": neutral_count,
        "negativeCount": negative_count,
        "totalCount": total_count,
    }


def _classify_sentiments(data) -> list[str]:
    """
    여러 응답을 한 번에
    POSITIVE / NEUTRAL / NEGATIVE로 분류한다.
    """

    indexed_data = [
        {
            "index": index,
            **item
        }
        for index, item in enumerate(data)
    ]

    prompt = f"""
당신은 사용자 설문 응답의 감정을 분류하는 AI입니다.

아래에는 여러 개의 설문 질문과 답변이 있습니다.

각 항목을 개별적으로 분석하여
POSITIVE, NEUTRAL, NEGATIVE 중 하나로 분류하세요.

판단 기준:

POSITIVE
- 만족
- 긍정적인 평가
- 좋은 경험
- 높은 만족도
- 호의적인 답변

NEUTRAL
- 중립적인 의견
- 보통 수준
- 명확한 긍정 또는 부정이 없음
- 감정 판단이 어려움

NEGATIVE
- 불만
- 불편함
- 개선 요구
- 낮은 만족도
- 부정적인 경험

중요:
- 질문과 답변을 함께 고려하세요.
- SCORE 유형이라면 점수의 의미도 고려하세요.
- 각 index는 반드시 그대로 유지하세요.
- 입력 항목 개수와 결과 항목 개수는 반드시 같아야 합니다.
- 모든 입력 항목에 대해 하나씩 결과를 반환하세요.

반드시 아래 JSON 형식만 반환하세요.

{{
  "results": [
    {{
      "index": 0,
      "sentiment": "POSITIVE"
    }},
    {{
      "index": 1,
      "sentiment": "NEGATIVE"
    }}
  ]
}}

sentiment 값은 반드시
POSITIVE, NEUTRAL, NEGATIVE 중 하나여야 합니다.

JSON 외의 설명이나 마크다운은 출력하지 마세요.

분석 데이터:
{json.dumps(indexed_data, ensure_ascii=False, indent=2)}
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "당신은 여러 사용자 응답의 감정을 분류하는 AI입니다. "
                    "각 응답을 빠짐없이 긍정, 중립, 부정으로 분류하고 "
                    "반드시 JSON 형식으로만 응답하세요."
                )
            ),
            HumanMessage(content=prompt)
        ]
    )

    content = _remove_code_block(
        response.content.strip()
    )

    result = json.loads(content)

    if "results" not in result:
        raise ValueError(
            "AI 응답에 results 값이 없습니다."
        )

    results = result["results"]

    if not isinstance(results, list):
        raise ValueError(
            "AI 감정 분석 results는 배열이어야 합니다."
        )

    if len(results) != len(data):
        raise ValueError(
            "AI 감정 분석 결과 개수가 입력 데이터 개수와 다릅니다."
        )

    # index 순으로 다시 정렬
    results = sorted(
        results,
        key=lambda item: int(item["index"])
    )

    sentiments = []

    for expected_index, item in enumerate(results):

        if int(item["index"]) != expected_index:
            raise ValueError(
                "AI 감정 분석 결과의 index가 올바르지 않습니다."
            )

        sentiment = str(
            item.get("sentiment", "")
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

        sentiments.append(sentiment)

    return sentiments


def _remove_code_block(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]

    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()