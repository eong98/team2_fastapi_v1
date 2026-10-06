from typing import Optional

from pydantic import BaseModel, Field


class ShopSurveyOptionStat(BaseModel):
    """보기별 선택 수"""

    label: str
    count: int


class ShopSurveyQuestionStat(BaseModel):
    """문항별 집계 (Spring에서 기간 필터 적용 후 전달)"""

    question: str
    type: str  # SHORT / LONG / SINGLE / MULTI / SCALE
    answerCount: int = 0
    scaleAvg: Optional[float] = None
    scaleMax: Optional[int] = None
    options: list[ShopSurveyOptionStat] = Field(default_factory=list)


class ShopSurveyTextAnswer(BaseModel):
    """서술형 답변 원문"""

    question: str
    answer: str


class ShopSurveySummaryRequest(BaseModel):
    """매장 설문 AI 요약 요청 (Spring ShopSurveyService.summarize)"""

    shopTitle: Optional[str] = None
    surveyTitle: str
    totalResponses: int
    questions: list[ShopSurveyQuestionStat] = Field(default_factory=list)
    textAnswers: list[ShopSurveyTextAnswer] = Field(default_factory=list)


class ShopSurveySummaryResponse(BaseModel):
    """매장 설문 AI 요약 결과"""

    summary: str
    score: float  # 0 = 매우 부정, 5 = 중립, 10 = 매우 긍정
    reason: str
    weakPoints: list[str] = Field(default_factory=list)  # 약한 항목 (AI 자동작성 참고용)