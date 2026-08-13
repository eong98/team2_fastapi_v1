from fastapi import APIRouter, HTTPException

from survey.schema import SurveyAnalysisResponse
from survey.service import analyze_survey


router = APIRouter(
    prefix="/api/survey",
    tags=["Survey AI"]
)


@router.post(
    "/{survey_no}/analyze",
    response_model=SurveyAnalysisResponse
)
def analyze(survey_no: int):
    """
    특정 설문의 전체 응답을 AI로 분석한다.

    처리 순서:
    1. Oracle DB에서 설문 응답 조회
    2. AI 종합 점수화
    3. AI 감정 분석
    4. AI 요약 분석
    5. SURVEYANALYSIS 저장
    6. 분석 결과 반환
    """

    try:
        return analyze_survey(survey_no)

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"설문 AI 분석 실패: {str(e)}"
        )