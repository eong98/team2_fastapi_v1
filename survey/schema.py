from pydantic import BaseModel


class SurveyAnalysisResponse(BaseModel):
    """설문 AI 분석 결과 응답 모델"""

    surveyNo: int
    aiScore: float

    positiveRate: float
    neutralRate: float
    negativeRate: float

    summary: str
    positiveSummary: str
    negativeSummary: str