import os
from datetime import datetime
from pathlib import Path

import cv2

from core.database import get_connection
from core.codes_cache import get_codes

# =========================================================
# 실행 환경 확인
# =========================================================

IS_H200 = os.path.exists("/home/d260730-c1-s2")


# =========================================================
# 저장 경로
# =========================================================

if IS_H200:
    # H200 서버
    SHOPMAP_DIR = Path(
        "/home/d260730-c1-s2/team2_jpa_v1/src/main/resources/static/shopmap/storage"
    )

    AIISSUEMAP_DIR = Path(
        "/home/d260730-c1-s2/team2_jpa_v1/src/main/resources/static/aiissuemap/storage"
    )

else:
    # Windows 개발 환경
    BASE_DIR = Path(__file__).resolve().parent

    SHOPMAP_DIR = BASE_DIR.parent / "shopmap" / "storage"
    AIISSUEMAP_DIR = BASE_DIR / "storage"


# AI 이슈 도면 저장 폴더 생성
AIISSUEMAP_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 좌표 검증
# =========================================================


def check_position(xpos: float | None, ypos: float | None):
    """CCTV에서 전달받은 x, y 좌표가 0~1 범위인지 확인한다."""

    if xpos is None or ypos is None:
        return False, "좌표 정보가 없습니다."

    if not (0 <= xpos <= 1 and 0 <= ypos <= 1):
        return False, f"좌표가 0~1 범위를 벗어났습니다: ({xpos}, {ypos})"

    return True, None


# =========================================================
# CCTV 이슈 코드 → 색상
# =========================================================


def get_issue_color(code: str) -> str:
    """
    CCTV_ISSUE_CODE의 severity 값을 기준으로
    AI 이슈 도면에 표시할 색상을 결정한다.
    """

    codes = get_codes()
    issue_info = codes.get(code)

    if issue_info is None:
        raise ValueError(f"등록되지 않은 CCTV 이슈 코드입니다: {code}")

    if issue_info.get("useYn", "Y") != "Y":
        raise ValueError(f"사용 중지된 CCTV 이슈 코드입니다: {code}")

    severity = issue_info.get("severity")

    color_map = {
        "높음": "#FF0000",
        "보통": "#FFA500",
        "낮음": "#00C853",
    }

    color = color_map.get(severity)

    if color is None:
        raise ValueError(f"지원하지 않는 이슈 위험도입니다: {severity}")

    return color


# =========================================================
# 매장번호로 SHOPMAP 조회
# =========================================================


def find_shopmap_no(sno: int) -> int | None:
    """매장번호로 등록된 최신 원본 도면 번호를 조회한다."""

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT NO
            FROM SHOPMAP
            WHERE SNO = :sno
            ORDER BY NO DESC
            FETCH FIRST 1 ROWS ONLY
            """,
            {"sno": sno},
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return int(row[0])

    finally:
        cursor.close()
        conn.close()


# =========================================================
# SHOPMAP 원본 이미지 조회
# =========================================================


def find_original_shopmap(shopmapno: int):
    """
    SHOPMAP 테이블에서 저장 파일명을 조회하고
    실제 원본 도면 이미지를 읽어 반환한다.
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT FSAVED
            FROM SHOPMAP
            WHERE NO = :no
            """,
            {"no": shopmapno},
        )

        row = cursor.fetchone()

        if row is None:
            return None

        fsaved = row[0]

    finally:
        cursor.close()
        conn.close()

    if not fsaved:
        return None

    file_path = SHOPMAP_DIR / fsaved

    if not file_path.exists():
        return None

    return cv2.imread(str(file_path))


# =========================================================
# AIISSUEMAP 저장
# =========================================================


def save_ai_issue_map(
    shopmapno: int,
    cino: int,
    xpos: float | None,
    ypos: float | None,
    color: str | None,
    fsaved: str | None,
    status: int,
    err: str | None = None,
):
    """
    생성된 AI 이슈 도면 정보를 AIISSUEMAP 테이블에 저장한다.

    SMNO : SHOPMAP.NO
    CINO : CCTV_ISSUE.NO
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:
        no_var = cursor.var(int)

        cursor.execute(
            """
            INSERT INTO AIISSUEMAP
                (
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
            VALUES
                (
                    SEQ_AIISSUEMAP_NO.NEXTVAL,
                    :smno,
                    :cino,
                    :xpos,
                    :ypos,
                    :color,
                    :fsaved,
                    :status,
                    :err,
                    :cdate
                )
            RETURNING NO INTO :no
            """,
            {
                "smno": shopmapno,
                "cino": cino,
                "xpos": xpos,
                "ypos": ypos,
                "color": color,
                "fsaved": fsaved,
                "status": status,
                "err": err,
                "cdate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "no": no_var,
            },
        )

        conn.commit()

        value = no_var.getvalue()

        if isinstance(value, (list, tuple)):
            value = value[0]

        return int(value)

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# =========================================================
# HEX → OpenCV BGR 변환
# =========================================================


def hex_to_bgr(hex_color: str):
    """#RRGGBB 색상값을 OpenCV에서 사용하는 BGR 값으로 변환한다."""

    hex_color = hex_color.lstrip("#")

    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    return b, g, r


# =========================================================
# CCTV → AI 이슈 도면 처리 시작점
# =========================================================


def process_cctv_issue_map(
    no: int,
    code: str,
    sno: int,
    x: float | None = None,
    y: float | None = None,
) -> dict:
    """
    CCTV 이슈 정보를 이용하여 매장 원본 도면에
    이슈 위치를 표시한 AI 이슈 도면을 생성한다.

    no   : CCTV_ISSUE.NO
    code : CCTV 이슈 코드
    sno  : 매장번호
    x, y : 0~1 비율 좌표
    """

    # 매장에 등록된 원본 도면 조회
    shopmapno = find_shopmap_no(sno)

    # 도면이 없는 경우 AIISSUEMAP을 생성하지 않는다.
    if shopmapno is None:
        return {
            "success": True,
            "aimapno": None,
            "smno": None,
            "cino": no,
            "sno": sno,
            "code": code,
            "color": None,
            "status": None,
            "fsaved": None,
            "message": "등록된 매장 도면이 없어 이슈 위치 이미지를 생성하지 않았습니다.",
        }

    return create_issue_map(
        shopmapno=shopmapno,
        cino=no,
        code=code,
        xpos=x,
        ypos=y,
    )


# =========================================================
# AI 이슈 도면 생성
# =========================================================


def create_issue_map(
    shopmapno: int,
    cino: int,
    code: str,
    xpos: float | None = None,
    ypos: float | None = None,
):
    """원본 매장 도면에 CCTV 이슈 위치를 점으로 표시한다."""

    # 좌표 검증
    valid, error = check_position(xpos, ypos)

    if not valid:
        aimapno = save_ai_issue_map(
            shopmapno=shopmapno,
            cino=cino,
            xpos=xpos,
            ypos=ypos,
            color=None,
            fsaved=None,
            status=2,
            err=error,
        )

        return {
            "success": False,
            "aimapno": aimapno,
            "smno": shopmapno,
            "cino": cino,
            "code": code,
            "color": None,
            "status": 2,
            "fsaved": None,
            "message": error,
        }

    try:
        # CCTV 이슈 위험도에 따른 표시 색상
        color = get_issue_color(code)

        # SHOPMAP 원본 이미지 조회
        image = find_original_shopmap(shopmapno)

        if image is None:
            error = "원본 매장 도면 이미지를 찾을 수 없습니다."

            aimapno = save_ai_issue_map(
                shopmapno=shopmapno,
                cino=cino,
                xpos=xpos,
                ypos=ypos,
                color=color,
                fsaved=None,
                status=2,
                err=error,
            )

            return {
                "success": False,
                "aimapno": aimapno,
                "smno": shopmapno,
                "cino": cino,
                "code": code,
                "color": color,
                "status": 2,
                "fsaved": None,
                "message": error,
            }

        height, width = image.shape[:2]

        # 0~1 비율 좌표 → 실제 이미지 픽셀 좌표
        px = int(round(xpos * (width - 1)))
        py = int(round(ypos * (height - 1)))

        # 이미지 범위를 벗어나지 않도록 보정
        px = max(0, min(width - 1, px))
        py = max(0, min(height - 1, py))

        bgr = hex_to_bgr(color)

        # 바깥쪽 흰색 원
        cv2.circle(
            image,
            (px, py),
            15,
            (255, 255, 255),
            -1,
            lineType=cv2.LINE_AA,
        )

        # 안쪽 이슈 색상 원
        cv2.circle(
            image,
            (px, py),
            10,
            bgr,
            -1,
            lineType=cv2.LINE_AA,
        )

        # 저장 파일명 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fsaved = f"issue_shopmap_{shopmapno}_" f"cctvissue_{cino}_{timestamp}.png"

        output_path = AIISSUEMAP_DIR / fsaved

        # AI 이슈 도면 이미지 저장
        success = cv2.imwrite(str(output_path), image)

        if not success:
            raise RuntimeError("AI 이슈 도면 이미지 저장에 실패했습니다.")

        # AIISSUEMAP DB 저장
        aimapno = save_ai_issue_map(
            shopmapno=shopmapno,
            cino=cino,
            xpos=xpos,
            ypos=ypos,
            color=color,
            fsaved=fsaved,
            status=1,
            err=None,
        )

        return {
            "success": True,
            "aimapno": aimapno,
            "smno": shopmapno,
            "cino": cino,
            "code": code,
            "color": color,
            "status": 1,
            "fsaved": fsaved,
            "message": "AI 이슈 도면이 생성되었습니다.",
        }

    except Exception as e:
        error = str(e)

        # 생성 실패 정보 저장
        aimapno = save_ai_issue_map(
            shopmapno=shopmapno,
            cino=cino,
            xpos=xpos,
            ypos=ypos,
            color=None,
            fsaved=None,
            status=2,
            err=error,
        )

        return {
            "success": False,
            "aimapno": aimapno,
            "smno": shopmapno,
            "cino": cino,
            "code": code,
            "color": None,
            "status": 2,
            "fsaved": None,
            "message": error,
        }


# =========================================================
# AIISSUEMAP 단건 조회
# =========================================================


def get_ai_issue_map(no: int):
    """AIISSUEMAP 번호로 생성 결과를 조회한다."""

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
            WHERE NO = :no
            """,
            {"no": no},
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return {
            "no": int(row[0]),
            "smno": int(row[1]) if row[1] is not None else None,
            "cino": int(row[2]) if row[2] is not None else None,
            "xpos": float(row[3]) if row[3] is not None else None,
            "ypos": float(row[4]) if row[4] is not None else None,
            "color": row[5],
            "fsaved": row[6],
            "status": int(row[7]) if row[7] is not None else None,
            "err": row[8],
            "cdate": row[9],
        }

    finally:
        cursor.close()
        conn.close()
