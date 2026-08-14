from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile
)
from pydantic import BaseModel

from shopmap.service import (
    create_issue_map,
    process_shopmap
)


router = APIRouter(
    prefix="/api/shopmap",
    tags=["ShopMap AI"]
)


# ========================================
# Request DTO
# ========================================

class ShopMapIssueRequest(BaseModel):
    """
    AI 이슈 도면 생성 요청
    """
    shopmapno: int
    issue: str
    xpos: float
    ypos: float


# ========================================
# 기본 AI 도면 생성
# ========================================

@router.post("/generate")
async def generate_shopmap(
    shopmapno: int = Form(...),
    file: UploadFile = File(...)
):
    """
    원본 매장 도면을 업로드하고
    기본 AI 도면을 생성한다.

    Local:
        storage/shopmap
        storage/aiissuemap

    H200:
        ~/allimio/storage/shopmap
        ~/allimio/storage/aiissuemap
    """

    try:

        result = await process_shopmap(
            shopmapno=shopmapno,
            file=file
        )

        return result

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"AI 도면 생성 실패: {str(e)}"
        )


# ========================================
# AI 이슈 도면 생성
# ========================================

@router.post("/issue")
def generate_issue_shopmap(
    request: ShopMapIssueRequest
):
    """
    발생한 이슈 내용을 AI가 분석한다.

    AI가:
    - 이슈 유형 판단
    - 위험도 판단
    - 색상 판단

    이후 기본 AI 도면의
    xpos, ypos 위치에 해당 색상의
    마커를 표시한다.
    """

    try:

        result = create_issue_map(
            shopmapno=request.shopmapno,
            issue=request.issue,
            xpos=request.xpos,
            ypos=request.ypos
        )

        return result

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"AI 이슈 도면 생성 실패: {str(e)}"
        )