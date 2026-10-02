from pathlib import Path
import shutil

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse

router = APIRouter(
    prefix="/api/shopmap",
    tags=["shopmap"],
)


# =========================================================
# H200 원본 도면 저장 경로
# =========================================================

SHOPMAP_DIR = Path("/data/home/d260730-c1-s2/allimio/storage/shopmap")

# 폴더가 없으면 자동 생성
SHOPMAP_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =========================================================
# 원본 도면 업로드
# =========================================================


@router.post("/upload")
async def upload_shopmap(
    file: UploadFile = File(...),
):
    """
    Java 서버에서 전달한 매장 원본 도면을
    H200 storage/shopmap 폴더에 저장한다.

    반환되는 filename은
    SHOPMAP.FSAVED에 저장한다.
    """

    # 파일명 확인
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="파일명이 없습니다.",
        )

    # 경로 조작 방지
    filename = Path(file.filename).name

    # 확장자 확인
    extension = Path(filename).suffix.lower()

    allowed_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="지원하지 않는 이미지 형식입니다.",
        )

    # -----------------------------------------------------
    # 동일 파일명이 이미 존재할 경우
    # filename_1.png, filename_2.png 형태로 저장
    # -----------------------------------------------------

    original_stem = Path(filename).stem
    suffix = Path(filename).suffix

    save_filename = filename
    save_path = SHOPMAP_DIR / save_filename

    count = 1

    while save_path.exists():

        save_filename = f"{original_stem}_{count}{suffix}"

        save_path = SHOPMAP_DIR / save_filename

        count += 1

    # -----------------------------------------------------
    # H200에 실제 파일 저장
    # -----------------------------------------------------

    try:

        with save_path.open("wb") as buffer:

            shutil.copyfileobj(
                file.file,
                buffer,
            )

    except Exception as e:

        # 저장 중 일부 파일이 만들어졌으면 제거
        if save_path.exists():
            try:
                save_path.unlink()
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail=f"도면 저장 실패: {e}",
        )

    finally:

        await file.close()

    print("[shopmap][upload][SUCCESS] " f"{save_path}")

    return {
        "success": True,
        "filename": save_filename,
    }


# =========================================================
# 원본 도면 조회
# =========================================================


@router.get("/file/{filename}")
def get_shopmap_file(
    filename: str,
):
    """
    H200에 저장된 원본 도면을 반환한다.

    Java의
    /api/shopmaps/view/{no}
    /api/shopmaps/admin/download/{no}

    에서 사용한다.
    """

    # 경로 조작 방지
    safe_filename = Path(filename).name

    file_path = SHOPMAP_DIR / safe_filename

    if not file_path.exists():

        print("[shopmap][file][NOT_FOUND] " f"{file_path}")

        raise HTTPException(
            status_code=404,
            detail="도면 파일이 없습니다.",
        )

    if not file_path.is_file():

        raise HTTPException(
            status_code=400,
            detail="올바른 도면 파일이 아닙니다.",
        )

    print("[shopmap][file][SUCCESS] " f"{file_path}")

    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
    )


# =========================================================
# 원본 도면 삭제
# =========================================================


@router.delete("/{filename}")
def delete_shopmap(
    filename: str,
):
    """
    H200 storage/shopmap에 저장된
    원본 매장 도면을 삭제한다.
    """

    # 경로 조작 방지
    safe_filename = Path(filename).name

    file_path = SHOPMAP_DIR / safe_filename

    # 이미 파일이 없는 경우
    # Java의 DB 삭제까지 실패시키지 않도록
    # 정상 처리한다.
    if not file_path.exists():

        print("[shopmap][delete][NOT_FOUND] " f"{file_path}")

        return {
            "success": True,
            "filename": safe_filename,
            "message": "이미 삭제된 파일입니다.",
        }

    if not file_path.is_file():

        raise HTTPException(
            status_code=400,
            detail="올바른 도면 파일이 아닙니다.",
        )

    # -----------------------------------------------------
    # 실제 파일 삭제
    # -----------------------------------------------------

    try:

        file_path.unlink()

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"도면 삭제 실패: {e}",
        )

    print("[shopmap][delete][SUCCESS] " f"{file_path}")

    return {
        "success": True,
        "filename": safe_filename,
        "message": "도면 파일이 삭제되었습니다.",
    }
