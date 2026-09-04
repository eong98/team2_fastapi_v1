from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from fastapi.responses import FileResponse

from pydantic import BaseModel

from shopmap.service import (
    AIISSUEMAP_DIR,
    create_issue_map,
    get_ai_issue_map,
    process_shopmap,
)

router = APIRouter(
    prefix="/api/shopmap",
    tags=["ShopMap AI"],
)


# ========================================
# Request DTO
# ========================================


class ShopMapIssueRequest(BaseModel):
    """
    AI 이슈 도면 생성 요청
    """

    shopmapno: int
    cino: int
    issue: str

    xpos: float | None = None
    ypos: float | None = None


# ========================================
# 기본 AI 도면 생성
# ========================================


@router.post("/generate")
async def generate_shopmap(
    shopmapno: int = Form(...),
    file: UploadFile = File(...),
):
    """
    원본 매장 도면을 업로드하고
    기본 AI 도면을 생성한다.
    """

    try:

        result = await process_shopmap(
            shopmapno=shopmapno,
            file=file,
        )

        return result

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=(f"AI 도면 생성 실패: " f"{str(e)}"),
        )


# ========================================
# AI 이슈 도면 생성
# ========================================


@router.post("/issue")
def generate_issue_shopmap(request: ShopMapIssueRequest):
    """
    발생한 이슈 내용을 AI가 분석한다.

    이후 기본 AI 도면의
    xpos, ypos 위치에 해당 색상의
    마커를 표시한다.
    """

    try:

        result = create_issue_map(
            shopmapno=request.shopmapno,
            cino=request.cino,
            issue=request.issue,
            xpos=request.xpos,
            ypos=request.ypos,
        )

        return result

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=("AI 이슈 도면 생성 실패: " f"{str(e)}"),
        )


# ========================================
# AI 이슈맵 정보 조회
# ========================================


@router.get("/issue-map/{aimapno}")
def get_issue_map_info(aimapno: int):
    """
    AIISSUEMAP.NO 기준으로
    AI 이슈맵 정보를 조회한다.

    React 알림 상세에서:
    NOTIFICATION.AIMAPNO
        ↓
    AIISSUEMAP.FSAVED
        ↓
    실제 이미지 조회

    용도로 사용한다.
    """

    try:

        result = get_ai_issue_map(aimapno=aimapno)

        if result is None:

            raise HTTPException(
                status_code=404,
                detail=("AI 이슈맵 정보를 " "찾을 수 없습니다."),
            )

        return result

    except HTTPException:
        raise

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=("AI 이슈맵 조회 실패: " f"{str(e)}"),
        )


# ========================================
# AI 이슈 도면 이미지 조회
# ========================================


@router.get("/image/{filename}")
def get_issue_shopmap_image(filename: str):
    """
    생성된 AI 이슈 도면 이미지를 조회한다.

    예:
    GET
    /api/shopmap/image/
    issue_shopmap_1_20260828_120000.png
    """

    # 파일명만 허용
    if "/" in filename or "\\" in filename or ".." in filename:

        raise HTTPException(
            status_code=400,
            detail="잘못된 파일명입니다.",
        )

    file_path = AIISSUEMAP_DIR / filename

    if not file_path.exists() or not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail=("AI 이슈 도면 이미지를 " "찾을 수 없습니다."),
        )

    return FileResponse(
        path=str(file_path),
        media_type="image/png",
    )
