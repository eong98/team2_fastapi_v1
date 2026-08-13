import json

import ollama


MODEL_NAME = "gemma4:26b"


def analyze_summary(data) -> dict:
    """입력 데이터를 분석하여 전체/긍정/부정 요약을 반환한다."""
    prompt = f"""
당신은 사용자 응답 데이터를 요약하는 AI입니다.
아래 데이터에는 점수형, 선택형, 주관식 등 다양한 형태의 응답이 포함될 수 있습니다.

전체 데이터를 종합적으로 분석하여 다음 내용을 작성하세요.
1. summary: 전체 데이터의 핵심 내용, 반복 의견과 전체 경향
2. positiveSummary: 긍정 평가, 만족 기능과 장점
3. negativeSummary: 불편사항, 부정 의견과 개선 요구사항

반드시 아래 JSON 형식만 반환하세요.
{{
  "summary": "전체 내용 종합 요약",
  "positiveSummary": "주요 긍정 내용 요약",
  "negativeSummary": "주요 부정 내용 요약"
}}
JSON 외의 설명이나 마크다운은 출력하지 마세요.

분석 데이터:
{json.dumps(data, ensure_ascii=False, indent=2)}
"""
    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "당신은 사용자 응답 데이터를 요약하는 AI입니다. 반드시 요청된 JSON 형식으로만 응답하세요."},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0.2},
    )
    content = _remove_code_block(response["message"]["content"].strip())
    result = json.loads(content)
    required_fields = ["summary", "positiveSummary", "negativeSummary"]
    for field in required_fields:
        if field not in result:
            raise ValueError(f"AI 응답에 {field} 값이 없습니다.")
    return {
        "summary": result["summary"],
        "positiveSummary": result["positiveSummary"],
        "negativeSummary": result["negativeSummary"],
    }


def _remove_code_block(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()
