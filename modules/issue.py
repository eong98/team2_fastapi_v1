import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


def analyze_issue(issue: str) -> dict:
    """
    이슈 내용을 AI가 분석하여
    이슈 유형, 위험도, 표시 색상을 결정한다.
    """

    if issue is None or not issue.strip():
        raise ValueError("분석할 이슈 내용이 없습니다.")

    prompt = f"""
당신은 무인매장 CCTV 이슈를 분석하는 AI입니다.

발생한 이슈 내용을 분석하여
도면에 표시할 정보를 결정하세요.

판단할 항목:

1. issueType
- 발생한 상황을 짧고 명확하게 분류합니다.
- 예: 화재 의심, 침입 의심, 고객 쓰러짐, 시설 이상 등

2. severity
- LOW
- MEDIUM
- HIGH
- CRITICAL

3. color
- 이슈의 종류와 위험도를 고려하여
  도면에서 직관적으로 구분하기 좋은 색상을 직접 결정합니다.
- 반드시 HEX 색상값으로 반환합니다.
- 형식은 #RRGGBB 입니다.
- 서로 다른 성격이나 위험도의 이슈를 시각적으로 구분할 수 있도록 판단합니다.

4. reason
- 왜 해당 유형, 위험도, 색상을 선택했는지 짧게 설명합니다.

중요:
- 색상은 미리 정해진 표에서 선택하는 것이 아닙니다.
- 당신이 이슈의 성격과 위험도를 분석하여 직접 결정하세요.
- JSON 외의 문장이나 마크다운을 절대 출력하지 마세요.

반드시 아래 JSON 형식으로만 반환하세요.

{{
  "issueType": "화재 의심",
  "severity": "CRITICAL",
  "color": "#FF0000",
  "reason": "연기 발생으로 즉각적인 확인이 필요한 상황"
}}

이슈 내용:
{issue}
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "당신은 무인매장 안전 이슈를 분석하는 AI입니다. "
                    "이슈의 유형과 위험도를 판단하고 "
                    "도면에 사용할 색상을 직접 결정합니다. "
                    "반드시 JSON만 반환하세요."
                )
            ),
            HumanMessage(content=prompt)
        ]
    )

    content = _remove_code_block(
        response.content.strip()
    )

    result = json.loads(content)

    issue_type = str(
        result.get("issueType", "")
    ).strip()

    severity = str(
        result.get("severity", "")
    ).strip().upper()

    color = str(
        result.get("color", "")
    ).strip().upper()

    reason = str(
        result.get("reason", "")
    ).strip()

    if not issue_type:
        raise ValueError(
            "AI가 이슈 유형을 반환하지 않았습니다."
        )

    if severity not in {
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL"
    }:
        raise ValueError(
            "AI 위험도 값이 올바르지 않습니다."
        )

    if not re.fullmatch(
        r"#[0-9A-F]{6}",
        color
    ):
        raise ValueError(
            "AI 색상값이 올바른 HEX 형식이 아닙니다."
        )

    return {
        "issueType": issue_type,
        "severity": severity,
        "color": color,
        "reason": reason
    }


def _remove_code_block(
    content: str
) -> str:

    if content.startswith("```json"):
        content = content[7:]

    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()