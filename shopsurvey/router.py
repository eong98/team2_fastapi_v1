from fastapi import APIRouter, HTTPException

from shopsurvey.agent import run_agent
from shopsurvey.schema import (
    ShopSurveyGenerateRequest,
    ShopSurveyGenerateResponse,
    ShopSurveySummaryRequest,
    ShopSurveySummaryResponse,
)
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



@router.post(
    "/generate",
    response_model=ShopSurveyGenerateResponse
)
def generate(req: ShopSurveyGenerateRequest):
    """
    매장 설문 AI 자동작성 (LangGraph 에이전트)

    - create : 요청 문장 + 이전 설문(refSvnos) + 약한 항목으로 새 설문 생성
    - revise : 현재 폼 + 수정 요청으로 고치기
    - trend  : 현재 폼에 업종 뉴스 트렌드 문항 1~2개 추가

    Spring(ShopSurveyAiClient)에서만 호출한다.
    refSvnos는 Spring이 점주 소유를 확인한 번호만 넘어오므로 여기서는 읽기만 한다.
    """

    if req.mode in ("create", "revise") and not req.request.strip():
        raise HTTPException(status_code=400, detail="요청 내용을 입력해주세요.")
    if req.mode in ("revise", "trend") and (req.currentForm is None or not req.currentForm.questions):
        raise HTTPException(status_code=400, detail="수정할 설문 문항이 없습니다.")

    try:
        return run_agent(
            mode=req.mode,
            request=req.request,
            shop_title=req.shopTitle,
            industry=req.industry,
            ref_svnos=req.refSvnos,
            current_form=req.currentForm,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"매장 설문 AI 자동작성 실패: {str(e)}")