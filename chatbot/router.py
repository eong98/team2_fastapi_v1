"""
chatbot/router.py

챗봇 상담 API — AI 상담 시작/질문/종료, 대화 요약, 실시간 알림(WebSocket).
옵션메뉴 자동생성 관리 API(/api/chatbot/manual-doc/*)는 chatbot/manual/router.py에 있고,
아래에서 이 라우터에 포함시킵니다.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import traceback

from chatbot.chat_repository import ChatRequestError
from chatbot.manual.router import router as manual_router
from chatbot.service import (
    end_ai_consult_divider,
    process_ai_chat,
    start_ai_consult,
    summarize_and_save,
    summarize_title_in_background,
)
from chatbot.ws_manager import ws_manager


# 라우터 설정
router = APIRouter(
    prefix="/api/chatbot",
    tags=["Chatbot AI"]
)


class AiChatRequest(BaseModel):
    message: str


@router.post("/{sno}/summarize")
def summarize(sno: str):
    """
    특정 세션의 대화 로그를 AI로 요약합니다.

    [처리 순서]
    1. Oracle DB에서 CHAT_LOG 조회
    2. Ollama(gemma)로 title/content/type 추출
    3. CHAT_SESSION.STITLE 저장 (채팅목록 타이틀)
    4. title/content/type 반환 (QA 등록 폼 초기값용)
    """
    try:
        return summarize_and_save(sno)
    except ChatRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"챗봇 대화 요약 실패: {str(e)}")


@router.post("/{sno}/summarize-title")
def summarize_title(sno: str, background_tasks: BackgroundTasks):
    """
    상담 종료 후 채팅 목록 제목만 AI 요약으로 바꿉니다. 백그라운드로 실행하고 바로 응답합니다.
    완료되면 WebSocket으로 {"type": "session_updated", "sno"} 알림 → 목록 새로고침.
    """
    background_tasks.add_task(summarize_title_in_background, sno)
    return {"accepted": True}


@router.put("/{sno}/ai-start")
def ai_start(sno: str):
    """
    AI 상담을 시작합니다. 
    구분선(고정 문구) + LLM이 생성한 인사말을 저장하고, 세션을 AI상담 모드로 전환합니다.
    """
    try:
        return start_ai_consult(sno)
    except ChatRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"AI 상담 시작 실패: {str(e)}")


@router.put("/{sno}/ai-end")
def ai_end(sno: str):
    """
    AI 상담 중단 구분선(고정 문구)을 저장합니다.
    다른질문하기 / 관리자문의 / 상담종료 버튼 클릭 시 먼저 호출됩니다.
    """
    try:
        return end_ai_consult_divider(sno)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"AI 상담 종료 처리 실패: {str(e)}")


@router.post("/{sno}/ai-chat")
async def ai_chat(sno: str, request: AiChatRequest):
    """
    AI 자유상담을 진행합니다.
    사용자 질문을 받아 매뉴얼 검색 기반으로 답변을 생성하고 CHAT_LOG에 직접 저장합니다.

    [처리 순서]
    1. 사용자 질문 CHAT_LOG 저장
    2. ChromaDB에서 매뉴얼 검색 -> Ollama로 답변 생성
    3. AI 답변 CHAT_LOG 저장
    4. 답을 못 찾았으면 CHAT_SESSION.ENDFLOW=4 저장 (관리자연결 버튼 상태 유지)
    5. { logs, needsAdmin } 반환 -> 프론트엔드가 화면에 대화 추가
    """
    try:
        return await process_ai_chat(sno, request.message)
    except ChatRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"AI 상담 응답 생성 실패: {str(e)}")


@router.websocket("/ws")
async def chat_ws(
    websocket: WebSocket,
    mno: int | None = Query(default=None),
    gno: str | None = Query(default=None),
):
    """
    챗봇 실시간 알림용 WebSocket 엔드포인트입니다.
    프론트엔드가 앱 진입 시 연결을 맺어두면, AI 답변 완료 즉시 실시간 알림을 받습니다.
    """
    await ws_manager.connect(websocket, mno, gno)
    try:
        while True:
            # 클라이언트 수신 메시지는 연결 유지 목적으로 대기 (필요 시 로직 추가 가능)
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, mno, gno)


# 옵션메뉴 관리 API (/api/chatbot/manual-doc/*)
router.include_router(manual_router)
