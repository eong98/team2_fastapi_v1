from fastapi import APIRouter, HTTPException, Response, status

from survey.schema import SurveyAnalysisResponse
from survey.service import (
    analyze_survey,
    get_survey_analysis,
)


router = APIRouter(
    prefix="/api/survey",
    tags=["Survey AI"]
)


# ========================================
# 1. 설문 AI 분석 실행
# ========================================

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

        print(
            f"[SURVEY][ANALYZE][START] "
            f"survey_no={survey_no}"
        )

        result = analyze_survey(survey_no)

        print(
            f"[SURVEY][ANALYZE][SUCCESS] "
            f"survey_no={survey_no}"
        )

        return result

    except ValueError as e:

        print(
            f"[SURVEY][ANALYZE][FAIL] "
            f"survey_no={survey_no}, "
            f"error={e}"
        )

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except Exception as e:

        print(
            f"[SURVEY][ANALYZE][ERROR] "
            f"survey_no={survey_no}, "
            f"error={e}"
        )

        raise HTTPException(
            status_code=500,
            detail=f"설문 AI 분석 실패: {str(e)}"
        )


# ========================================
# 2. 기존 설문 AI 분석 결과 조회
# ========================================

@router.get(
    "/{survey_no}/analysis",
    response_model=SurveyAnalysisResponse,
    responses={
        204: {
            "description": "저장된 AI 분석 결과 없음"
        }
    }
)
def get_analysis(survey_no: int):
    """
    SURVEYANALYSIS에 저장된 기존 AI 분석 결과를 조회한다.

    기존 분석 결과가 있으면:
        200 + 분석 결과 반환

    기존 분석 결과가 없으면:
        204 No Content

    분석 결과가 없는 것은 정상적인 상태이므로
    404 또는 500 오류로 처리하지 않는다.
    """

    try:

        print(
            f"[SURVEY][ANALYSIS][GET][START] "
            f"survey_no={survey_no}"
        )

        result = get_survey_analysis(survey_no)

        # ====================================
        # 아직 AI 분석을 실행하지 않은 설문
        # ====================================

        if result is None:

            print(
                f"[SURVEY][ANALYSIS][GET][EMPTY] "
                f"survey_no={survey_no}"
            )

            return Response(
                status_code=status.HTTP_204_NO_CONTENT
            )

        # ====================================
        # 기존 분석 결과 존재
        # ====================================

        print(
            f"[SURVEY][ANALYSIS][GET][SUCCESS] "
            f"survey_no={survey_no}"
        )

        return result

    except Exception as e:

        print(
            f"[SURVEY][ANALYSIS][GET][ERROR] "
            f"survey_no={survey_no}, "
            f"error={e}"
        )

        raise HTTPException(
            status_code=500,
            detail=f"설문 AI 분석 결과 조회 실패: {str(e)}"
        )from fastapi import APIRouter, HTTPException, Response, status

from survey.schema import SurveyAnalysisResponse
from survey.service import (
    analyze_survey,
    get_survey_analysis,
)


router = APIRouter(
    prefix="/api/survey",
    tags=["Survey AI"]
)


# ========================================
# 1. 설문 AI 분석 실행
# ========================================

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

        print(
            f"[SURVEY][ANALYZE][START] "
            f"survey_no={survey_no}"
        )

        result = analyze_survey(survey_no)

        print(
            f"[SURVEY][ANALYZE][SUCCESS] "
            f"survey_no={survey_no}"
        )

        return result

    except ValueError as e:

        print(
            f"[SURVEY][ANALYZE][FAIL] "
            f"survey_no={survey_no}, "
            f"error={e}"
        )

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except Exception as e:

        print(
            f"[SURVEY][ANALYZE][ERROR] "
            f"survey_no={survey_no}, "
            f"error={e}"
        )

        raise HTTPException(
            status_code=500,
            detail=f"설문 AI 분석 실패: {str(e)}"
        )


# ========================================
# 2. 기존 설문 AI 분석 결과 조회
# ========================================

@router.get(
    "/{survey_no}/analysis",
    response_model=SurveyAnalysisResponse,
    responses={
        204: {
            "description": "저장된 AI 분석 결과 없음"
        }
    }
)
def get_analysis(survey_no: int):
    """
    SURVEYANALYSIS에 저장된 기존 AI 분석 결과를 조회한다.

    기존 분석 결과가 있으면:
        200 + 분석 결과 반환

    기존 분석 결과가 없으면:
        204 No Content

    분석 결과가 없는 것은 정상적인 상태이므로
    404 또는 500 오류로 처리하지 않는다.
    """

    try:

        print(
            f"[SURVEY][ANALYSIS][GET][START] "
            f"survey_no={survey_no}"
        )

        result = get_survey_analysis(survey_no)

        # ====================================
        # 아직 AI 분석을 실행하지 않은 설문
        # ====================================

        if result is None:

            print(
                f"[SURVEY][ANALYSIS][GET][EMPTY] "
                f"survey_no={survey_no}"
            )

            return Response(
                status_code=status.HTTP_204_NO_CONTENT
            )

        # ====================================
        # 기존 분석 결과 존재
        # ====================================

        print(
            f"[SURVEY][ANALYSIS][GET][SUCCESS] "
            f"survey_no={survey_no}"
        )

        return result

    except Exception as e:

        print(
            f"[SURVEY][ANALYSIS][GET][ERROR] "
            f"survey_no={survey_no}, "
            f"error={e}"
        )

        raise HTTPException(
            status_code=500,
            detail=f"설문 AI 분석 결과 조회 실패: {str(e)}"
        )