"""
chatbot/manual/router.py

옵션형 메뉴 자동생성 관리 API — /api/chatbot/manual-doc/*
(chatbot/router.py가 이 라우터를 포함하므로 main.py는 수정할 필요 없음)
"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from chatbot.manual.category import replace_top_menus
from chatbot.manual.db import publish_menu_nodes, publish_menu_tree
from chatbot.manual.docs import delete_manual_doc, delete_manual_docs, get_manual_docs, is_generate_available, retry_vectorize_docs, update_manual_doc, upload_manual_doc
from chatbot.manual.generate import generate_menu_from_docs
from chatbot.manual.job import clear_generate_job, get_generate_job_status, regenerate_all_docs, start_generate_job, start_suggest_job
from chatbot.manual.progress import GenerateRunningError, ensure_not_generating


router = APIRouter(prefix="/manual-doc", tags=["Chatbot 옵션메뉴 관리"])


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


@router.post("")
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
@router.put("/publish-nodes")
def publish_nodes(request: PublishNodesRequest):
    """공용 카테고리 + 이번에 생성된 하위메뉴만 공개합니다(미리보기 [공개]/[일괄 공개])."""
    _block_while_generating()
    try:
        publish_menu_nodes(request.topNo, request.nos)
        return {"published": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"메뉴 공개 처리 실패: {str(e)}")


@router.put("/{doc_no:int}")  # :int — "top-menus" 같은 문자열 경로가 여기로 잡히지 않게
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


@router.get("/list")
def list_docs():
    """현재 업로드되어 있는 문서 전체 목록을 조회합니다."""
    try:
        return get_manual_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"문서 목록 조회 실패: {str(e)}")


@router.get("/generate-available")
def generate_available():
    """
    [AI 옵션생성] 버튼을 활성화해도 되는지 조회합니다.
    등록/수정/삭제로 아직 마지막 생성에 반영되지 않은 문서가 있으면 True.
    """
    try:
        return {"available": is_generate_available()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"상태 조회 실패: {str(e)}")


@router.post("/delete-bulk")
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


@router.delete("/{doc_no:int}")
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


@router.post("/generate-menu")
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


@router.post("/generate-menu/start")
def generate_menu_start():
    """
    [AI 옵션생성]을 백그라운드 작업으로 시작하고 바로 현재 상태를 반환합니다.
    이미 진행 중이면 새로 시작하지 않고 진행 중인 작업 상태를 돌려줍니다(중복 실행 방지).
    """
    try:
        return start_generate_job()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 옵션생성 시작 실패: {str(e)}")


@router.post("/generate-menu/regenerate-all")
def generate_menu_regenerate_all():
    """
    [매뉴얼 전체 다시 생성] 모든 문서를 미반영으로 되돌리고 옵션생성을 시작합니다.
    (내부 정보 제외, 긴 제목 요약 등 바뀐 규칙을 기존 AI 메뉴에 반영할 때)
    """
    _block_while_generating()
    try:
        return regenerate_all_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"전체 다시 생성 시작 실패: {str(e)}")


@router.get("/generate-menu/status")
def generate_menu_status():
    """
    AI 옵션생성 진행 상태. 화면 진입/새로고침 시와 진행 중 폴링에 사용합니다.
    { status: idle|running|done|error, percent, logs:[{key,message,state}], elapsedSec, result, error }
    """
    return get_generate_job_status()


@router.post("/generate-menu/clear")
def generate_menu_clear():
    """완료/실패한 작업 기록을 지웁니다(진행 패널 [닫기]). 진행 중이면 무시됩니다."""
    return clear_generate_job()


@router.post("/suggest-categories/start")
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


@router.put("/top-menus")
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


@router.post("/retry-vectorize")
def retry_vectorize():
    """
    [벡터화 재시도] 버튼에서 호출됩니다. CHAT_MENU 재생성 없이, 아직
    벡터화가 안 된 문서만 다시 벡터화를 시도합니다.
    """
    try:
        return retry_vectorize_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"벡터화 재시도 실패: {str(e)}")


@router.put("/publish/{top_no}")
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
