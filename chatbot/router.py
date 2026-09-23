from fastapi import APIRouter, HTTPException

from chatbot.service import summarize_and_save

router = APIRouter(
    prefix="/api/chatbot",
    tags=["Chatbot AI"]
)

# http://139.150.91.194:11241/docs#/Chatbot%20AI/summarize_api_chatbot__sno__summarize_post

@router.post("/{sno}/summarize")
def summarize(sno: str):
    """
    특정 세션의 대화 로그를 AI로 요약한다.

    처리 순서:
    1. Oracle DB에서 CHAT_LOG 조회
    2. Ollama(gemma)로 title/content/type 추출
    3. CHAT_SESSION.STITLE 저장 (채팅목록 타이틀)
    4. title/content/type 반환 (QA 등록 폼 초기값용)
    """
    try:
        return summarize_and_save(sno)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"챗봇 대화 요약 실패: {str(e)}")



"""
chatbot/router.py에 추가되는 엔드포인트.

기존 @router.post("/{sno}/summarize") 아래에 이어서 추가하면 됩니다.
"""

from chatbot.service import start_ai_consult, end_ai_consult_divider

@router.put("/{sno}/ai-start")
async def ai_start(sno: str):
    """
    AI 상담 시작. 구분선(고정 문구) + LLM이 생성한 인사말을 저장하고,
    세션을 AI상담 모드로 전환합니다.
    """
    try:
        return start_ai_consult(sno)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 상담 시작 실패: {str(e)}")

@router.put("/{sno}/ai-end")
async def ai_end(sno: str):
    """AI상담 중단 구분선(고정 문구) 저장. 다른질문하기/관리자문의/상담종료 클릭 시 먼저 호출됩니다."""
    try:
        return end_ai_consult_divider(sno)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 상담 종료 처리 실패: {str(e)}")
    

from pydantic import BaseModel
from chatbot.service import process_ai_chat


class AiChatRequest(BaseModel):
    message: str


@router.post("/{sno}/ai-chat")
async def ai_chat(sno: str, request: AiChatRequest):
    """
    AI 자유상담. 사용자 질문을 받아 매뉴얼 검색 기반으로 답변을 생성하고,
    CHAT_LOG에 직접 저장합니다 (Spring을 거치지 않음).

    처리 순서:
    1. 사용자 질문 CHAT_LOG 저장
    2. ChromaDB에서 매뉴얼 검색 → Ollama로 답변 생성
    3. AI 답변 CHAT_LOG 저장
    4. 답을 못 찾았으면 CHAT_SESSION.ENDFLOW=4 저장 (관리자연결 버튼 상태 유지)
    5. { logs, needsAdmin } 반환 — 프론트가 그대로 화면에 이어붙임
    """
    try:
        return await process_ai_chat(sno, request.message)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 상담 응답 생성 실패: {str(e)}")



from fastapi import WebSocket, WebSocketDisconnect, Query

from chatbot.ws_manager import ws_manager


@router.websocket("/ws")
async def chat_ws(
    websocket: WebSocket,
    mno: int | None = Query(default=None),
    gno: str | None = Query(default=None),
):
    """
    챗봇 실시간 알림용 WebSocket. 프론트가 앱 진입 시 이 연결을 맺어두면,
    AI 답변이 도착하는 즉시 { type: "new_message", sno: "..." } 형태로 알림을 받습니다.
    """
    await ws_manager.connect(websocket, mno, gno)
    try:
        while True:
            await websocket.receive_text()  # 클라이언트로부터의 메시지는 지금은 무시(연결 유지 목적)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, mno, gno)