import traceback

from fastapi import APIRouter, Depends, HTTPException

from chatbot.ticket import require_server
from chatstats.schema import ChatStatsSummaryRequest, ChatStatsSummaryResponse
from chatstats.service import summarize_chat_stats

router = APIRouter(
    prefix="/api/chat-stats",
    tags=["Chat Stats AI"],
)


# ========================================
# 챗봇 상담 만족도 AI 분석
# ========================================


@router.post("/summary", response_model=ChatStatsSummaryResponse, dependencies=[Depends(require_server)])
def summary(req: ChatStatsSummaryRequest):
    """
    챗봇 상담 만족도 조사 결과 요약 + 긍정/부정 점수(0~10)

    Spring(ChatStatsAiClient)에서만 호출한다.
    관리자 확인, 기간 필터, 집계는 Spring이 끝낸 뒤 데이터를 넘기므로
    여기서는 DB를 읽지 않고 LLM만 호출한다.
    티켓 검사가 켜져 있으면 Spring 서버 티켓이 있어야 한다.
    """
    try:
        return summarize_chat_stats(req)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"챗봇 만족도 AI 분석 실패: {str(e)}")
