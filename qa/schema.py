from pydantic import BaseModel, Field

# ========================================
# 1:1 문의 AI 자동 답변 요청 / 응답
# ========================================


class QaAnswerRequest(BaseModel):
    """Spring이 문의 등록 직후 보내는 문의 제목·내용"""

    title: str = Field("", description="문의 제목 (QA.TITLE)")
    content: str = Field("", description="문의 내용 (QA.CONTENT)")


class QaAnswerResponse(BaseModel):
    """answered=False면 Spring은 답변을 등록하지 않고 관리자 답변 대기로 둠"""

    answered: bool = Field(..., description="매뉴얼로 답할 수 있어 답변을 만들었는지")
    answer: str = Field("", description="답변 내용 (answered=False면 빈 문자열)")
