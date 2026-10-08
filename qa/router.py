import traceback

from fastapi import APIRouter, HTTPException

from qa.schema import QaAnswerRequest, QaAnswerResponse
from qa.service import answer_for_qa

router = APIRouter(
    prefix="/api/qa",
    tags=["QA AI"],
)


# ========================================
# 1:1 문의 AI 자동 답변
# ========================================


@router.post("/ai-answer", response_model=QaAnswerResponse)
def qa_ai_answer(request: QaAnswerRequest):
    """
    1:1 문의 AI 자동 답변 — Spring(QaAiAnswerService)이 문의 등록 직후 백그라운드로 호출.

    챗봇 AI 상담과 같은 매뉴얼 검색·답변 생성을 쓰고,
    매뉴얼에 없는 내용이면 answered=False (Spring은 관리자 답변 대기로 둠).
    """
    try:
        return answer_for_qa(request.title, request.content)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=503, detail="AI 답변을 만들지 못했습니다.")
