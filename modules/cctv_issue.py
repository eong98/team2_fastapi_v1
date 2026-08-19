"""
CCTV 이상행동 "설명 다듬기" 모듈.

⚠️ 이 모듈은 더 이상 유형(CODE)을 재분류하지 않는다.

기존 버전은 LLM한테 "폭행/쓰러짐 중 뭐야?"를 다시 물어봤는데, 서버 LLM은 텍스트만
읽을 수 있고 영상을 볼 수 없어서 실제로는 Jetson이 보낸 문장을 그대로 다시 카테고리로
욱여넣는 것에 불과했다(=진짜 재검증이 아니라 눈속임에 가까움). 영상 기반 판단은 오직
Jetson(YOLO/휴리스틱)만 할 수 있으므로, CODE는 Jetson이 확정한 값을 그대로 신뢰한다.

이 모듈이 실제로 하는 일은 두 가지뿐이다.
  1) code가 CODE_MAP에 있는 유효한 값인지 검증한다 (Jetson 쪽 버그로 이상한 값이
     오는 걸 막는 방어 로직).
  2) Jetson이 보낸 근거 텍스트(detail)를 관리자가 CCTV_ISSUE 목록에서 바로 읽을
     수 있는 자연스러운 한국어 문장(comnet)으로 다듬는다.

reliability(신뢰도)는 LLM이 새로 매기지 않고 Jetson이 계산해서 보낸 confidence를
그대로 사용한다(0~1 -> 0~100 변환만 함). 이 부분도 "서버가 재판단"하는 것처럼
보이면 안 되기 때문이다.

modules/issue.py(shopmap 도면용)와 달리, 여기서는 LLM 호출이 실패해도 이슈 등록
자체가 막히면 안 된다(사람이 다칠 수 있는 안전 이벤트라서). 그래서 이 모듈에서
예외가 나면 호출부(cctv/router.py)가 detail을 그대로 comnet으로 써서 저장을
계속 진행하도록 설계돼 있다. 이 모듈 자체는 여전히 실패 시 예외를 던진다
(그래야 호출부가 "LLM 문장 다듬기 실패"를 감지하고 폴백할 수 있다).
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


# ⚠️ 참조 테이블이 없어서 프론트(CctvIssue.ts CODE_LABELS), cctv/service.py의
#    CODE_* 상수와 동일하게 맞춰둔 임시 매핑. 실제 코드 테이블이 생기면 이걸
#    DB 조회로 바꾸고, 세 군데(프론트/서버 service/여기) 전부 같이 고쳐야 한다.
CODE_MAP = {
    "01": "폭행",
    "02": "기물파손",
    "03": "쓰러짐/응급",
    "04": "무단침입",
    "05": "장시간체류",
}


def analyze_cctv_issue(code: str, detail: str, confidence: float) -> dict:
    """
    Jetson이 이미 확정한 code/confidence를 검증하고, detail을 관리자용 문장으로 다듬는다.

    반환:
        {
            "code": 입력받은 code 그대로 (검증만 함),
            "reliability": confidence * 100 (0~100, Jetson 값 그대로),
            "comnet": LLM이 다듬은 상황설명 문장
        }
    """

    code = str(code).strip()

    if code not in CODE_MAP:
        raise ValueError(f"Jetson이 보낸 code 값이 올바르지 않습니다: {code!r}")

    if not detail or not detail.strip():
        raise ValueError("상황 설명(detail)이 없습니다.")

    if not 0 <= confidence <= 1:
        raise ValueError("confidence는 0~1 사이여야 합니다.")

    label = CODE_MAP[code]

    prompt = f"""
당신은 무인매장 CCTV 이상행동 이벤트를 관리자용 문장으로 정리하는 AI입니다.

아래는 에지 디바이스(Jetson)의 AI 모델이 영상을 직접 분석해서 이미 "{label}"으로
확정한 이벤트의 탐지 근거입니다. 당신은 이 유형을 다시 판단하지 않습니다.
(영상을 볼 수 없으므로 유형 판단은 하지 않고, 아래 근거를 자연스러운 한국어
문장으로 정리하는 역할만 합니다.)

확정된 유형: {label}
탐지 근거: {detail}
AI 신뢰도: {round(confidence * 100)}%

comnet 작성 규칙:
- 관리자가 목록에서 바로 읽을 수 있는 한두 문장으로 작성하세요.
- "~것으로 감지되었습니다" 같은 표현으로, 이 문장이 AI 탐지 결과라는 걸 드러내세요.
- 근거에 없는 내용을 추측해서 덧붙이지 마세요.

반드시 아래 JSON 형식만 반환하세요.

{{
  "comnet": "두 사람 간 급격한 신체 접촉과 움직임이 감지되어 폭행 상황으로 추정됩니다."
}}

JSON 외의 설명이나 마크다운은 출력하지 마세요.
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "당신은 CCTV 이상행동 탐지 근거를 관리자용 한국어 문장으로 "
                    "정리하는 AI입니다. 유형을 재판단하지 말고, 반드시 JSON 형식으로만 응답하세요."
                )
            ),
            HumanMessage(content=prompt)
        ]
    )

    content = _remove_code_block(response.content.strip())

    result = json.loads(content)

    comnet = str(result.get("comnet", "")).strip()

    if not comnet:
        raise ValueError("AI가 상황설명(comnet)을 반환하지 않았습니다.")

    return {
        "code": code,
        "reliability": round(confidence * 100),
        "comnet": comnet,
    }


def _remove_code_block(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]

    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()
