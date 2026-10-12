import traceback

from fastapi import APIRouter, Depends, HTTPException

from chatbot.ticket import require_server
from faq.schema import FaqSuggestRequest, FaqSuggestResponse
from faq.service import suggest_faq

router = APIRouter(
    prefix="/api/faq",
    tags=["FAQ AI"],
)


# ========================================
# FAQ AI 추천
# ========================================


@router.post("/suggest", response_model=FaqSuggestResponse, dependencies=[Depends(require_server)])
def suggest(req: FaqSuggestRequest):
    """
    자주 들어오는 1:1 문의를 묶어 FAQ 후보를 만든다.

    Spring(QaFaqSuggestClient)에서만 호출한다.
    관리자 확인, 기간 필터, 문의 조회는 Spring이 끝낸 뒤 데이터를 넘기므로
    여기서는 DB를 읽지 않고 임베딩과 LLM만 호출한다.
    결과는 후보일 뿐이고, 관리자가 확인·수정한 뒤 FAQ로 등록한다.
    """
    try:
        return suggest_faq(req)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"FAQ AI 추천 실패: {str(e)}")
