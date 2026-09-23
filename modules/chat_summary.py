import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


def summarize_chat(conversation_text: str) -> dict:
    """
    상담 대화 텍스트를 분석하여 문의글 title/content/type을 반환한다.
    """

    if not conversation_text:
        raise ValueError("요약할 대화 내용이 없습니다.")

    prompt = f"""
당신은 챗봇 상담 대화를 분석하여 고객센터 문의글로 정리하는 AI입니다.

아래 대화 내용의 각 줄은 [메시지유형] 발화자: 내용 형식입니다.
메시지유형의 의미는 다음과 같습니다:
- 옵션선택: 사용자가 미리 정해진 선택지를 클릭한 것 (실제로 타이핑한 질문이 아님)
- 옵션답변: 그 선택지에 대한 고정 안내문 (시스템이 미리 정해둔 답변)
- 자유질문: 사용자가 직접 입력한 자유 텍스트 질문 (가장 중요하게 참고할 부분)
- AI답변: AI가 자유질문에 대해 생성한 답변
- 시스템안내: 만족도조사, 관리자연결 안내 같은 시스템 메시지 (문의 내용과 무관, 무시)
- 뒤로가기: 사용자가 "다른 질문하기"를 누른 것 (무시)

문의글 작성 시:
- "자유질문"과 "AI답변"을 중심으로 사용자가 실제로 무엇을 궁금해했는지 파악하세요.
- "옵션선택"이 있었다면, 사용자가 대화를 어떤 주제로 시작했고 그 내용을 문의글에 포함시키세요.
- "시스템안내"와 "뒤로가기"는 문의 내용에 포함하지 마세요.
- "자유질문"에서 문의유형과 관계 없어보이는 내용은 문의 내용에 포함하지 마세요.
- 사용자가 직접 관리자에게 문의하는 것처럼 작성하세요.

아래는 실제 상담 대화입니다.
이 대화를 바탕으로 관리자에게 전달할 문의글을 작성하세요.

1. title
- 문의 내용을 한눈에 알 수 있는 짧은 제목 (20자 이내)

2. content
- 사용자가 무엇을 많이 클릭하고 문의했는지 요약한 내용

3. type
- 문의 유형을 아래 중 하나의 숫자로 분류
- 0: 기타
- 1: 관제신청
- 2: 영상요청
- 3: 장비장애
- 4: 구독권
- 5: 회원가입
- 6: 로그인
- 7: CCTV
- 8: 직원초대

반드시 아래 JSON 형식만 반환하세요.

{{
  "title": "문의 제목",
  "content": "문의 내용 요약",
  "type": 8
}}

JSON 외의 설명이나 마크다운은 출력하지 마세요.
"type"의 값은 반드시 3.type 중 하나여야 합니다.

대화 내용:
{conversation_text}
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "당신은 상담 대화를 분석하여 문의글을 작성하는 AI입니다. "
                    "반드시 요청된 JSON 형식으로만 응답하세요."
                )
            ),
            HumanMessage(content=prompt)
        ]
    )

    content = _remove_code_block(response.content.strip())

    result = json.loads(content)

    required_fields = ["title", "content", "type"]
    for field in required_fields:
        if field not in result:
            raise ValueError(f"AI 응답에 {field} 값이 없습니다.")

    return {
        "title": str(result["title"])[:200],
        "content": str(result["content"])[:1000],
        "type": int(result["type"]),
    }


def _remove_code_block(content: str) -> str:
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    return content.strip()