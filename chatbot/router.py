from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, UploadFile, File
from pydantic import BaseModel

from chatbot.service import (
    end_ai_consult_divider,
    process_ai_chat,
    start_ai_consult,
    summarize_and_save,
)
from chatbot.ws_manager import ws_manager
 
from chatbot.manual_doc import (
    upload_manual_doc,
    update_manual_doc,
    get_manual_docs,
    delete_manual_doc,
    delete_manual_docs,
    generate_menu_from_docs,
    start_generate_job,
    get_generate_job_status,
    clear_generate_job,
    retry_vectorize_docs,
    publish_menu_tree,
    publish_menu_nodes,
    start_suggest_job,
    replace_top_menus,
    ensure_not_generating,
    GenerateRunningError,
    is_generate_available,
)
 

# 라우터 설정
router = APIRouter(
    prefix="/api/chatbot",
    tags=["Chatbot AI"]
)


# =====================================================================
# Request DTO Schema
# =====================================================================
class AiChatRequest(BaseModel):
    message: str


class DocBulkDeleteRequest(BaseModel):
    nos: list[int]


class TopMenuItem(BaseModel):
    label: str
    desc: str = ""


class ReplaceTopMenusRequest(BaseModel):
    menus: list[TopMenuItem]


class PublishNodesRequest(BaseModel):
    topNo: int
    nos: list[int]


def _block_while_generating() -> None:
    """AI 옵션생성 중이면 409로 거부 (화면에서도 막지만 API 직접 호출까지 차단)."""
    try:
        ensure_not_generating()
    except GenerateRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))


# =====================================================================
# REST API Endpoints
# =====================================================================

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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"챗봇 대화 요약 실패: {str(e)}")


@router.put("/{sno}/ai-start")
def ai_start(sno: str):
    """
    AI 상담을 시작합니다. 
    구분선(고정 문구) + LLM이 생성한 인사말을 저장하고, 세션을 AI상담 모드로 전환합니다.
    """
    try:
        return start_ai_consult(sno)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 상담 응답 생성 실패: {str(e)}")


# =====================================================================
# WebSocket Endpoint
# =====================================================================

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



 
# =====================================================================
# 옵션형 메뉴 자동생성 — 문서 업로드/조회/수정/삭제
# =====================================================================
 
@router.post("/manual-doc")
async def upload_doc(file: UploadFile = File(...)):
    """
    옵션형 메뉴 자동생성에 쓸 md 문서를 업로드합니다.
    관리자 메뉴관리 화면(ChatMenu.tsx)에서 [문서 첨부] 시 호출됩니다.
    """
    _block_while_generating()
    try:
        file_bytes = await file.read()
        return upload_manual_doc(file.filename, file_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"문서 업로드 실패: {str(e)}")
 
 
# "/manual-doc/{doc_no:int}"는 숫자만 받으므로 publish-nodes, top-menus 같은 경로와 충돌하지 않음
@router.put("/manual-doc/publish-nodes")
def publish_nodes(request: PublishNodesRequest):
    """공용 카테고리 + 이번에 생성된 하위메뉴만 공개합니다(미리보기 [공개]/[일괄 공개])."""
    _block_while_generating()
    try:
        publish_menu_nodes(request.topNo, request.nos)
        return {"published": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"메뉴 공개 처리 실패: {str(e)}")


@router.put("/manual-doc/{doc_no:int}")  # :int — "top-menus" 같은 문자열 경로가 여기로 잡히지 않게
async def update_doc(doc_no: int, file: UploadFile = File(...)):
    """
    이미 업로드된 문서를 새 파일로 교체합니다(같은 NO 유지).
    """
    _block_while_generating()
    try:
        file_bytes = await file.read()
        return update_manual_doc(doc_no, file.filename, file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"문서 수정 실패: {str(e)}")
 
 
@router.get("/manual-doc/list")
def list_docs():
    """현재 업로드되어 있는 문서 전체 목록을 조회합니다."""
    try:
        return get_manual_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"문서 목록 조회 실패: {str(e)}")
 
 
@router.get("/manual-doc/generate-available")
def generate_available():
    """
    [AI 옵션생성] 버튼을 활성화해도 되는지 조회합니다.
    등록/수정/삭제로 아직 마지막 생성에 반영되지 않은 문서가 있으면 True.
    """
    try:
        return {"available": is_generate_available()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"상태 조회 실패: {str(e)}")
 
 
@router.post("/manual-doc/delete-bulk")
def delete_docs_bulk(request: DocBulkDeleteRequest):
    """
    첨부 문서를 일괄 삭제합니다. 각 문서로 생성된 AI 옵션메뉴도 함께 삭제됩니다.
    반환: { deleted: [no...], failed: [{no, error}] }
    """
    _block_while_generating()
    if not request.nos:
        raise HTTPException(status_code=400, detail="삭제할 문서를 선택해주세요.")
    try:
        return delete_manual_docs(request.nos)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"문서 일괄삭제 실패: {str(e)}")


@router.delete("/manual-doc/{doc_no:int}")
def delete_doc(doc_no: int):
    """업로드된 문서를 삭제합니다. 이 문서로 생성된 AI 옵션메뉴도 함께 삭제됩니다."""
    _block_while_generating()
    try:
        delete_manual_doc(doc_no)
        return {"deleted": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"문서 삭제 실패: {str(e)}")
 
 
# =====================================================================
# 옵션형 메뉴 자동생성 — AI 생성 / 공개 전환
# =====================================================================
 
@router.post("/manual-doc/generate-menu")
def generate_menu():
    """
    신규 등록/수정된 문서(UPDATEYN='N')만 RAG 기반으로 분석해서, 주제별
    최상위 카테고리와 그 하위(STEP2~3) 메뉴를 CHAT_MENU에 USEYN='N'(비공개)으로 생성합니다.
    (진행률이 필요하면 /manual-doc/generate-menu/start + /status 사용)
 
    처리 순서:
    1. 대상 문서의 청크를 벡터DB에서 읽음 (벡터화 안 된 문서는 먼저 재시도)
    2. 문서별 주제 분석 → 주제별 유사도 검색 문맥으로 하위 트리 생성(병렬)
    3. CHAT_MENU에 순서대로 INSERT (모두 USEYN='N', 비공개)
    4. 응답에 categories(생성된 트리)와 vectorizeFailedDocs(끝내 벡터화
       실패한 문서 목록)를 함께 반환. vectorizeFailedDocs가 비어있지
       않으면, 프론트는 categories 미리보기를 보여주지 말고 "옵션 생성은
       완료되었으나 벡터화가 실패하였습니다" 안내와 함께
       [벡터화 재시도] 버튼을 유도해야 한다.
    """
    try:
        return generate_menu_from_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 메뉴 생성 실패: {str(e)}")
 
 
@router.post("/manual-doc/generate-menu/start")
def generate_menu_start():
    """
    [AI 옵션생성]을 백그라운드 작업으로 시작하고 바로 현재 상태를 반환합니다.
    이미 진행 중이면 새로 시작하지 않고 진행 중인 작업 상태를 돌려줍니다(중복 실행 방지).
    """
    try:
        return start_generate_job()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 옵션생성 시작 실패: {str(e)}")


@router.get("/manual-doc/generate-menu/status")
def generate_menu_status():
    """
    AI 옵션생성 진행 상태. 화면 진입/새로고침 시와 진행 중 폴링에 사용합니다.
    { status: idle|running|done|error, percent, logs:[{key,message,state}], elapsedSec, result, error }
    """
    return get_generate_job_status()


@router.post("/manual-doc/generate-menu/clear")
def generate_menu_clear():
    """완료/실패한 작업 기록을 지웁니다(진행 패널 [닫기]). 진행 중이면 무시됩니다."""
    return clear_generate_job()


@router.post("/manual-doc/suggest-categories/start")
def suggest_top_menus_start():
    """
    [최상위 메뉴 AI 추천]을 백그라운드로 시작합니다(저장 안 함). 진행 상황과 결과는
    /manual-doc/generate-menu/status 로 조회합니다(kind='suggest', result에 추천 목록).
    옵션생성과 같은 작업 틀을 쓰므로 둘 중 하나가 진행 중이면 새로 시작하지 않습니다.
    """
    try:
        return start_suggest_job()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"최상위 메뉴 추천 시작 실패: {str(e)}")


@router.put("/manual-doc/top-menus")
def replace_top_menus_api(request: ReplaceTopMenusRequest):
    """
    [추천 메뉴 등록] 최상위 메뉴 전체를 선택한 목록으로 교체합니다.
    같은 이름은 유지, 빠진 메뉴는 하위까지 삭제, 매뉴얼 전체는 [AI 옵션생성] 대상으로 되돌립니다.
    """
    _block_while_generating()
    try:
        return replace_top_menus([m.model_dump() for m in request.menus])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"최상위 메뉴 교체 실패: {str(e)}")


@router.post("/manual-doc/retry-vectorize")
def retry_vectorize():
    """
    [벡터화 재시도] 버튼에서 호출됩니다. CHAT_MENU 재생성 없이, 아직
    벡터화가 안 된 문서만 다시 벡터화를 시도합니다.
    """
    try:
        return retry_vectorize_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"벡터화 재시도 실패: {str(e)}")
 
 
@router.put("/manual-doc/publish/{top_no}")
def publish_menu(top_no: int):
    """
    관리자가 미리보기를 검토(및 필요시 텍스트 수정)한 뒤 [공개] 버튼을 누르면
    호출됩니다. 해당 최상위 메뉴와 그 하위 전체를 USEYN='Y'(공개)로 전환합니다.
    """
    try:
        publish_menu_tree(top_no)
        return {"published": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"메뉴 공개 처리 실패: {str(e)}")

