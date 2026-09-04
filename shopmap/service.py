import socket

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from core.database import get_connection
from modules.issue import analyze_issue

# ========================================
# 저장 경로 설정
# ========================================

H200_IP = "139.150.91.194"


def is_h200():

    try:
        ip = socket.gethostbyname(socket.gethostname())
        return ip == H200_IP

    except Exception:
        return False


if is_h200():

    # 원본 매장 도면
    SHOPMAP_DIR = Path.home() / "allimio" / "shopmap"

    # 이슈 위치가 표시된 결과 이미지
    AIISSUEMAP_DIR = Path.home() / "allimio" / "aiissuemap"

else:

    SHOPMAP_DIR = Path(r"C:\kd\deploy\allimio\shopmap")

    AIISSUEMAP_DIR = Path(r"C:\kd\deploy\allimio\aiissuemap")


SHOPMAP_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

AIISSUEMAP_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ========================================
# 좌표 검사
# ========================================


def check_position(
    xpos,
    ypos,
):
    """
    0 ~ 1 비율 좌표인지 검사
    """

    try:

        if xpos is None or ypos is None:
            return 0, 0, False

        xpos = float(xpos)
        ypos = float(ypos)

        if not (0 <= xpos <= 1 and 0 <= ypos <= 1):
            return 0, 0, False

        return xpos, ypos, True

    except (TypeError, ValueError):

        return 0, 0, False


# ========================================
# AIISSUEMAP DB 저장
# ========================================


def save_ai_issue_map(
    smno,
    cino,
    xpos,
    ypos,
    color,
    fsaved,
    status,
    err,
):
    """
    원본 도면에 이슈 위치를 표시한
    결과 정보를 AIISSUEMAP에 저장
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        # AIISSUEMAP PK 생성
        cursor.execute("""
            SELECT SEQ_AIISSUEMAP_NO.NEXTVAL
            FROM DUAL
            """)

        aimapno = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO AIISSUEMAP (
                NO,
                SMNO,
                CINO,
                XPOS,
                YPOS,
                COLOR,
                FSAVED,
                STATUS,
                ERR,
                CDATE
            )
            VALUES (
                :no,
                :smno,
                :cino,
                :xpos,
                :ypos,
                :color,
                :fsaved,
                :status,
                :err,
                TO_CHAR(
                    SYSDATE,
                    'YYYY-MM-DD HH24:MI:SS'
                )
            )
            """,
            {
                "no": aimapno,
                "smno": smno,
                "cino": cino,
                "xpos": xpos,
                "ypos": ypos,
                "color": color,
                "fsaved": fsaved,
                "status": status,
                "err": err,
            },
        )

        conn.commit()

        return aimapno

    except Exception:

        conn.rollback()
        raise

    finally:

        cursor.close()
        conn.close()


# ========================================
# 원본 도면 등록
# ========================================


async def process_shopmap(
    shopmapno,
    file,
):
    """
    원본 매장 도면 저장

    AI용 별도 도면은 생성하지 않는다.
    """

    extension = Path(file.filename).suffix.lower()

    if extension not in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }:

        raise ValueError("이미지 파일만 사용할 수 있습니다.")

    # 같은 SHOPMAP 번호의 기존 이미지 제거
    for old_file in SHOPMAP_DIR.glob(f"shopmap_{shopmapno}.*"):

        try:
            old_file.unlink()

        except Exception:
            pass

    original_filename = f"shopmap_{shopmapno}{extension}"

    original_path = SHOPMAP_DIR / original_filename

    content = await file.read()

    original_path.write_bytes(content)

    return {
        "success": True,
        "shopmapno": shopmapno,
        "file": original_filename,
    }


# ========================================
# 원본 도면 찾기
# ========================================


def find_original_shopmap(
    shopmapno: int,
):
    """
    SHOPMAP 번호를 기준으로
    DB에서 원본 도면 파일명(FSAVED)을 조회한 뒤
    실제 원본 이미지 파일 경로를 반환한다.

    도면이 등록되지 않았거나
    실제 파일이 존재하지 않는 경우에는
    예외를 발생시키지 않고 None을 반환한다.

    ※ 도면이 없는 경우 AIISSUEMAP은 생성하지 않는다.
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        # ----------------------------------------
        # SHOPMAP 번호로 원본 도면 파일명 조회
        # ----------------------------------------

        cursor.execute(
            """
            SELECT FSAVED
            FROM SHOPMAP
            WHERE NO = :shopmapno
            """,
            {
                "shopmapno": shopmapno,
            },
        )

        row = cursor.fetchone()

        # ----------------------------------------
        # 등록된 SHOPMAP 데이터가 없는 경우
        # ----------------------------------------

        if not row:
            return None

        fsaved = row[0]

        # ----------------------------------------
        # DB에 저장된 원본 파일명이 없는 경우
        # ----------------------------------------

        if not fsaved:
            return None

        # ----------------------------------------
        # 실제 원본 도면 저장 경로 생성
        # ----------------------------------------

        if is_h200():

            file_path = Path.home() / "allimio" / "shopmap" / "storage" / fsaved

        else:

            file_path = Path(r"C:\kd\deploy\allimio\shopmap\storage") / fsaved

        # ----------------------------------------
        # DB에는 파일명이 있지만
        # 실제 파일이 없는 경우
        # ----------------------------------------

        if not file_path.exists():
            return None

        # ----------------------------------------
        # 원본 도면 정상 조회
        # ----------------------------------------

        return file_path

    finally:

        cursor.close()
        conn.close()


# ========================================
# HEX → BGR
# ========================================


def hex_to_bgr(
    color,
):
    """
    #FF0000 등의 HEX 색상을
    OpenCV BGR 색상으로 변경
    """

    if color is None or not isinstance(color, str):

        return (0, 0, 255)

    color = color.lstrip("#")

    if len(color) != 6:

        return (0, 0, 255)

    try:

        return (
            int(color[4:6], 16),
            int(color[2:4], 16),
            int(color[0:2], 16),
        )

    except ValueError:

        return (0, 0, 255)


# ========================================
# 이슈 위치 이미지 생성
# ========================================


def create_issue_map(
    shopmapno,
    cino,
    issue,
    xpos=None,
    ypos=None,
):
    """
    원본 매장 도면에
    CCTV 이슈 위치를 표시하고
    AIISSUEMAP에 결과를 저장한다.
    """

    # ------------------------------------
    # 좌표 확인
    # ------------------------------------

    xpos, ypos, position_ok = check_position(
        xpos,
        ypos,
    )

    # ------------------------------------
    # 좌표 추출 실패
    # ------------------------------------

    if not position_ok:

        aimapno = save_ai_issue_map(
            smno=shopmapno,
            cino=cino,
            xpos=0,
            ypos=0,
            color=None,
            fsaved=None,
            status=2,
            err="좌표 추출 실패",
        )

        return {
            "success": False,
            "aimapno": aimapno,
            "smno": shopmapno,
            "cino": cino,
            "status": 2,
            "fsaved": None,
            "message": "좌표 추출 실패",
        }

    try:

        # ------------------------------------
        # 원본 도면 찾기
        # ------------------------------------

        original_path = find_original_shopmap(shopmapno)

        # ------------------------------------
        # 원본 도면이 없는 경우
        # ------------------------------------
        # 도면이 등록되지 않은 매장은
        # 이슈 위치 이미지를 생성하지 않는다.
        #
        # AIISSUEMAP 데이터도 생성하지 않는다.
        #
        # 이후 NOTIFICATION 생성 시
        # ASMNO는 NULL로 저장하면 된다.
        # ------------------------------------

        if original_path is None:

            return {
                "success": True,
                "aimapno": None,
                "smno": shopmapno,
                "cino": cino,
                "status": 1,
                "fsaved": None,
                "message": "등록된 매장 도면이 없어 이슈 위치 이미지를 생성하지 않았습니다.",
            }

        # ------------------------------------
        # 이슈 분석
        # ------------------------------------

        result = analyze_issue(issue)

        color = result.get("color")

        if not color:
            color = "#FF0000"

        # ------------------------------------
        # 원본 이미지 읽기
        # ------------------------------------

        image_data = np.fromfile(
            str(original_path),
            dtype=np.uint8,
        )

        image = cv2.imdecode(
            image_data,
            cv2.IMREAD_COLOR,
        )

        if image is None:

            raise ValueError("원본 매장 도면을 읽을 수 없습니다.")

        height, width = image.shape[:2]

        # ------------------------------------
        # 비율 좌표 → 실제 픽셀 좌표
        # ------------------------------------

        point_x = int(xpos * (width - 1))

        point_y = int(ypos * (height - 1))

        marker_color = hex_to_bgr(color)

        # ------------------------------------
        # 이슈 위치 표시
        # ------------------------------------

        # 흰색 외곽 원
        cv2.circle(
            image,
            (point_x, point_y),
            15,
            (255, 255, 255),
            -1,
        )

        # 이슈 색상 점
        cv2.circle(
            image,
            (point_x, point_y),
            10,
            marker_color,
            -1,
        )

        # ------------------------------------
        # 결과 파일명 생성
        # ------------------------------------

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        filename = f"issue_shopmap_" f"{shopmapno}_" f"{timestamp}.png"

        output_path = AIISSUEMAP_DIR / filename

        # ------------------------------------
        # 결과 이미지 저장
        # ------------------------------------

        success, encoded = cv2.imencode(
            ".png",
            image,
        )

        if not success:

            raise ValueError("이슈 위치 이미지 생성 실패")

        encoded.tofile(str(output_path))

        # ------------------------------------
        # AIISSUEMAP DB 저장
        # ------------------------------------

        aimapno = save_ai_issue_map(
            smno=shopmapno,
            cino=cino,
            xpos=xpos,
            ypos=ypos,
            color=color,
            fsaved=filename,
            status=1,
            err=None,
        )

        return {
            "success": True,
            "aimapno": aimapno,
            "smno": shopmapno,
            "cino": cino,
            "status": 1,
            "fsaved": filename,
        }

    except Exception as e:

        # ------------------------------------
        # 처리 실패 정보 저장
        # ------------------------------------

        aimapno = save_ai_issue_map(
            smno=shopmapno,
            cino=cino,
            xpos=xpos,
            ypos=ypos,
            color=None,
            fsaved=None,
            status=2,
            err=str(e)[:1000],
        )

        return {
            "success": False,
            "aimapno": aimapno,
            "smno": shopmapno,
            "cino": cino,
            "status": 2,
            "fsaved": None,
            "message": str(e),
        }


# ========================================
# AIISSUEMAP 정보 조회
# ========================================


def get_ai_issue_map(
    aimapno: int,
) -> dict | None:
    """
    AIISSUEMAP.NO 기준으로
    결과 이미지 정보를 조회한다.

    NOTIFICATION.ASMNO
        ↓
    AIISSUEMAP.NO
        ↓
    AIISSUEMAP.FSAVED
        ↓
    실제 이미지 조회
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            SELECT
                NO,
                SMNO,
                CINO,
                XPOS,
                YPOS,
                COLOR,
                FSAVED,
                STATUS,
                ERR,
                CDATE
            FROM AIISSUEMAP
            WHERE NO = :aimapno
            """,
            {
                "aimapno": aimapno,
            },
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return {
            "no": row[0],
            "smno": row[1],
            "cino": row[2],
            "xpos": row[3],
            "ypos": row[4],
            "color": row[5],
            "fsaved": row[6],
            "status": row[7],
            "err": row[8],
            "cdate": row[9],
        }

    finally:

        cursor.close()
        conn.close()
