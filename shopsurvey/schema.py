from typing import Literal, Optional

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

# =====================================================================
# AI 설문 자동작성 (POST /api/shop-survey/generate)
# =====================================================================

ATYPES = ("SHORT", "LONG", "SINGLE", "MULTI", "SCALE")


class GenOption(BaseModel):
    """객관식 보기"""

    label: str = Field(description="보기 내용 (손님이 이해하기 쉬운 짧은 말)")


class GenQuestion(BaseModel):
    """문항 (React 설문 폼 / Spring ShopSurveyDTO.Question과 같은 형식)"""

    title: str = Field(description="문항 제목, 한 문장")
    atype: Literal["SHORT", "LONG", "SINGLE", "MULTI", "SCALE"] = Field(
        description="SHORT=단답, LONG=장문, SINGLE=객관식 하나 선택, MULTI=객관식 여러 개 선택, SCALE=0~10점"
    )
    requiredyn: Literal[0, 1] = Field(default=0, description="필수 응답 여부 0/1")
    fileyn: Literal[0, 1] = Field(default=0, description="사진 첨부 허용 0/1 (청결·파손처럼 사진이 도움될 때만 1)")
    options: list[GenOption] = Field(
        default_factory=list, description="SINGLE/MULTI일 때만 2~8개, 나머지 타입은 빈 배열"
    )


class GenForm(BaseModel):
    """설문 폼 (React 폼 state = 임시저장 JSON = AI 출력 형식)"""

    title: str = Field(description="설문 제목")
    description: Optional[str] = Field(default="", description="손님에게 보여줄 짧은 안내 문구")
    questions: list[GenQuestion] = Field(default_factory=list)


class ShopSurveyGenerateRequest(BaseModel):
    """
    AI 설문 자동작성 요청 (Spring ShopSurveyAiClient.generate)

    mode
    - create : 요청 문장 + 점주가 고른 이전 설문(refSvnos)으로 새 설문 생성
    - revise : 현재 폼(currentForm) + 수정 요청 문장으로 고치기
    - trend  : 현재 폼 + 업종 뉴스 트렌드로 문항 1~2개 추가
    """

    mode: Literal["create", "revise", "trend"]
    request: str = ""
    shopTitle: Optional[str] = None
    industry: Optional[str] = None
    refSvnos: list[int] = Field(default_factory=list)
    currentForm: Optional[GenForm] = None


class NewsArticle(BaseModel):
    """트렌드 추가 때 참고한 기사"""

    title: str
    link: Optional[str] = None
    source: Optional[str] = None
    date: Optional[str] = None
    content: Optional[str] = None   # 기사 요약 (Tavily)


class ShopSurveyGenerateResponse(BaseModel):
    """AI 설문 자동작성 결과"""

    industry: str
    form: GenForm
    notes: list[str] = Field(default_factory=list)          # 이렇게 만든/고친 이유
    articles: list[NewsArticle] = Field(default_factory=list)  # trend 모드 참고 기사
    addedIndexes: list[int] = Field(default_factory=list)   # trend 모드에서 새로 추가된 문항 위치(0부터)
    message: Optional[str] = None                           # 안내 문구 (예: 트렌드 기사 없음)