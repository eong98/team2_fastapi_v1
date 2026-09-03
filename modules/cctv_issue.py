# -*- coding: utf-8 -*-
"""
modules/cctv_issue.py

Jetson이 보낸 판단 근거(detail, 영어/규칙 기반 문구)를 관리자가 보기 좋은 한국어 문장(comnet)으로
다듬는다. LLM 호출이 실패해도 detail을 그대로 comnet으로 써서 이슈 저장 자체는 계속 진행한다
(안전 이벤트라 LLM 문제로 저장이 막히면 안 됨 - design 문서 아키텍처 결정 참고).

Jetson이 이미 code(01/03/04)를 확정해서 보내주므로, 여기서는 code를 재분류하지 않는다.
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm

llm = get_llm()

_CODE_LABELS = {
    "01": "폭행",
    "02": "기물파손",
    "03": "쓰러짐/응급",
    "04": "무단침입",
    "05": "장시간체류",
}


def polish_detail_to_comnet(code: str, detail: str) -> str:
    """
    Jetson이 보낸 규칙 기반 판단 근거(detail)를 관리자용 한국어 문장(comnet)으로 다듬는다.
    실패 시 detail을 그대로 반환한다(재판단 없음, 저장은 계속 진행).
    """

    if not detail or not detail.strip():
        return f"{_CODE_LABELS.get(code, code)} 감지"

    label = _CODE_LABELS.get(code, code)

    prompt = f"""
당신은 무인매장 CCTV 이상행동 알림 문구를 다듬는 AI입니다.

Jetson 엣지 장치가 이미 "{label}"(코드 {code})로 상황을 판단했습니다.
아래는 그 판단의 근거(영어/규칙 기반 문구가 섞인 원문)입니다.

중요:
- 이미 확정된 상황 종류를 다시 판단하거나 바꾸지 마세요. 문장만 다듬으세요.
- 관리자가 한눈에 상황을 이해할 수 있도록 자연스러운 한국어 한 문장으로 바꾸세요.
- 과장하거나 새로운 정보를 추가하지 마세요.

반드시 아래 JSON 형식만 반환하세요.

{{"comnet": "여기에 한국어 문장"}}

JSON 외의 설명이나 마크다운은 출력하지 마세요.

판단 근거 원문:
{detail}
"""

    try:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "당신은 CCTV 이상행동 알림 문구를 관리자용 한국어로 다듬는 AI입니다. "
                        "상황 종류는 이미 확정되어 있으므로 재판단하지 말고 문장만 다듬으세요. "
                        "반드시 JSON만 반환하세요."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )

        content = _remove_code_block(response.content.strip())
        result = json.loads(content)
        comnet = str(result.get("comnet", "")).strip()

        if not comnet:
            raise ValueError("comnet 값이 비어있습니다.")

        return comnet

    except Exception as e:
        # LLM 실패해도 이슈 저장은 계속 진행 (design 문서 아키텍처 결정)
        print(f"[cctv_issue] comnet 다듬기 실패, 원문 사용: {e}")
        return f"[{label}] {detail}"


def _remove_code_block(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()
