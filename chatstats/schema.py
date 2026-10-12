from pydantic import BaseModel, Field

# ========================================
# 챗봇 상담 만족도 AI 분석 요청 / 응답
# ========================================


class ChatStatsCount(BaseModel):
    """항목별 건수 (불만족 사유 / 종료 사유)"""

    label: str
    count: int


class ChatStatsSummaryRequest(BaseModel):
    """
    챗봇 상담 만족도 AI 분석 요청 (Spring ChatStatsService.analyze)

    Spring이 관리자 확인·기간 필터·집계를 끝낸 뒤 데이터를 넘기므로
    여기서는 DB를 읽지 않고 LLM만 호출한다.
    """

    period: str = Field(..., description="조회 기간 (예: 2026-09-13 ~ 2026-10-12)")
    closed: int = Field(0, description="기간 내 종료된 상담 수")
    answered: int = Field(0, description="만족도 응답 수")
    satisfied: int = Field(0, description="만족 응답 수")
    dissatisfied: int = Field(0, description="불만족 응답 수")
    adminConnect: int = Field(0, description="관리자 문의로 연결된 상담 수")
    autoClosed: int = Field(0, description="30분 방치로 자동 종료된 상담 수 (만족도 미응답)")
    reasons: list[ChatStatsCount] = Field(default_factory=list, description="불만족 사유별 건수")
    opinions: list[str] = Field(default_factory=list, description="불만족 고객이 직접 쓴 의견")
    questions: list[str] = Field(default_factory=list, description="불만족 상담에서 고객이 고른 메뉴·입력한 질문")


class ChatStatsSummaryResponse(BaseModel):
    """챗봇 상담 만족도 AI 분석 결과"""

    summary: str
    score: float  # 0 = 매우 부정, 5 = 중립, 10 = 매우 긍정
    reason: str
    weakPoints: list[str] = Field(default_factory=list)  # 개선할 점
