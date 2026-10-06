from fastapi import APIRouter, HTTPException

from shopsurvey.schema import ShopSurveySummaryRequest, ShopSurveySummaryResponse
from shopsurvey.service import summarize_shop_survey


router = APIRouter(
    prefix="/api/shop-survey",
    tags=["Shop Survey AI"]
)


@router.post(
    "/summary",
    response_model=ShopSurveySummaryResponse
)
def summary(req: ShopSurveySummaryRequest):
    """
    매장 설문 응답 요약 + 긍정/부정 점수(0~10)

    Spring(ShopSurveyAiClient)에서만 호출한다.
    점주 권한 확인, 기간 필터, 응답 수집은 Spring이 끝낸 뒤 데이터를 넘기므로
    여기서는 DB를 읽지 않고 LLM만 호출한다.
    """

    try:
        return summarize_shop_survey(req)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"매장 설문 AI 요약 실패: {str(e)}")