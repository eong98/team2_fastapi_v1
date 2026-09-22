from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from aiissuemap.service import (
    AIISSUEMAP_DIR,
    create_issue_map,
    get_ai_issue_map,
)

router = APIRouter(
    prefix="/api/aiissuemap",
    tags=["AI Issue Map"],
)


# ========================================
# Request DTO
# ========================================


class AiIssueMapRequest(BaseModel):
    """AI 이슈 도면 생성 요청"""

    shopmapno: int
    cino: int
    code: str

    xpos: float | None = None
    ypos: float | None = None


# ========================================
# AI 이슈 도면 생성
# ========================================


@router.post("/issue")
def generate_issue_map(request: AiIssueMapRequest):
    """
    SHOPMAP 원본 도면에 CCTV 이슈 위치를 표시한다.

    shopmapno : SHOPMAP.NO
    cino      : CCTV_ISSUE.NO
    code      : CCTV 이슈 코드
    xpos/ypos : 0~1 비율 좌표
    """

    try:
        return create_issue_map(
            shopmapno=request.shopmapno,
            cino=request.cino,
            code=request.code,
            xpos=request.xpos,
            ypos=request.ypos,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"AI 이슈 도면 생성 실패: {str(e)}",
        )


# ========================================
# AIISSUEMAP 정보 조회
# ========================================


@router.get("/{no}")
def get_issue_map_info(no: int):
    """AIISSUEMAP.NO 기준으로 생성된 이슈 도면 정보를 조회한다."""

    try:
        result = get_ai_issue_map(no)

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="AI 이슈 도면 정보를 찾을 수 없습니다.",
            )

        return result

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"AI 이슈 도면 조회 실패: {str(e)}",
        )


# ========================================
# AI 이슈 도면 이미지 조회
# ========================================


@router.get("/image/{filename}")
def get_issue_map_image(filename: str):
    """생성된 AI 이슈 도면 이미지 파일을 조회한다."""

    # 경로 조작 방지
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(
            status_code=400,
            detail="잘못된 파일명입니다.",
        )

    file_path = AIISSUEMAP_DIR / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="AI 이슈 도면 이미지를 찾을 수 없습니다.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="image/png",
    )
