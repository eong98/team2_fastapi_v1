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
    SHOPMAP_DIR = Path.home() / "allimio" / "shopmap"
    AIISSUEMAP_DIR = Path.home() / "allimio" / "aiissuemap"
else:
    SHOPMAP_DIR = Path(r"C:\kd\deploy\allimio\shopmap")
    AIISSUEMAP_DIR = Path(r"C:\kd\deploy\allimio\aiissuemap")


SHOPMAP_DIR.mkdir(parents=True, exist_ok=True)
AIISSUEMAP_DIR.mkdir(parents=True, exist_ok=True)


# ========================================
# 좌표 검사
# ========================================


def check_position(xpos, ypos):
    """좌표가 없거나 잘못된 경우 0으로 실패 처리"""

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
# 회원번호 조회
# ========================================


def get_member_no(shopmapno):
    """매장 도면 번호로 점주 회원번호 조회"""

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT S.MNO
            FROM SHOPMAP SM
            JOIN SHOP S ON SM.SNO = S.NO
            WHERE SM.NO = :shopmapno
        """,
            {"shopmapno": shopmapno},
        )

        row = cursor.fetchone()

        if row is None:
            raise ValueError("매장 정보를 찾을 수 없습니다.")

        return row[0]

    finally:
        cursor.close()
        conn.close()


# ========================================
# AI 이슈맵 DB 저장
# ========================================


def save_ai_issue_map(mno, smno, xpos, ypos, color, fsaved, status, err):
    """AI 이슈맵 생성 결과 저장"""

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 저장할 AI 이슈맵 번호 생성
        cursor.execute("SELECT SEQ_AIISSUEMAP_NO.NEXTVAL FROM DUAL")

        aimapno = cursor.fetchone()[0]

        # AI 이슈맵 저장
        cursor.execute(
            """
            INSERT INTO AIISSUEMAP (
                NO,
                MNO,
                SMNO,
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
                :mno,
                :smno,
                :xpos,
                :ypos,
                :color,
                :fsaved,
                :status,
                :err,
                TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI:SS')
            )
        """,
            {
                "no": aimapno,
                "mno": mno,
                "smno": smno,
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
# 기본 AI 도면 생성
# ========================================


def create_base_ai_map(original_path, output_path):
    """원본 도면을 기본 AI 도면으로 변환"""

    image_data = np.fromfile(str(original_path), dtype=np.uint8)

    image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("원본 도면을 읽을 수 없습니다.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(blurred, 50, 150)

    kernel = np.ones((2, 2), np.uint8)

    edges = cv2.dilate(edges, kernel, iterations=1)

    result = cv2.bitwise_not(edges)

    success, encoded = cv2.imencode(".png", result)

    if not success:
        raise ValueError("AI 도면 생성 실패")

    encoded.tofile(str(output_path))


# ========================================
# 원본 도면 등록
# ========================================


async def process_shopmap(shopmapno, file):
    """원본 도면 저장 및 기본 AI 도면 생성"""

    extension = Path(file.filename).suffix.lower()

    if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("이미지 파일만 사용할 수 있습니다.")

    original_filename = f"shopmap_{shopmapno}{extension}"

    original_path = SHOPMAP_DIR / original_filename

    content = await file.read()

    original_path.write_bytes(content)

    ai_filename = f"ai_shopmap_{shopmapno}.png"

    ai_path = AIISSUEMAP_DIR / ai_filename

    create_base_ai_map(original_path, ai_path)

    return {"success": True, "shopmapno": shopmapno, "aiFile": ai_filename}


# ========================================
# HEX → BGR
# ========================================


def hex_to_bgr(color):
    """HEX 색상을 OpenCV BGR로 변환"""

    color = color.lstrip("#")

    return (int(color[4:6], 16), int(color[2:4], 16), int(color[0:2], 16))


# ========================================
# AI 이슈맵 생성
# ========================================


def create_issue_map(shopmapno, issue, xpos=None, ypos=None):
    """AI 이슈맵 생성 후 DB 저장"""

    mno = get_member_no(shopmapno)

    # 좌표 확인
    xpos, ypos, position_ok = check_position(xpos, ypos)

    # ------------------------------------
    # 좌표 추출 실패
    # ------------------------------------

    if not position_ok:

        aimapno = save_ai_issue_map(
            mno=mno,
            smno=shopmapno,
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
            "status": 2,
            "message": "좌표 추출 실패",
        }

    try:

        # 기본 AI 도면
        base_path = AIISSUEMAP_DIR / f"ai_shopmap_{shopmapno}.png"

        if not base_path.exists():
            raise ValueError("기본 AI 도면이 없습니다.")

        # 이슈 분석
        result = analyze_issue(issue)

        color = result.get("color")

        # 이미지 읽기
        image_data = np.fromfile(str(base_path), dtype=np.uint8)

        image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

        if image is None:
            raise ValueError("AI 도면을 읽을 수 없습니다.")

        height, width = image.shape[:2]

        # 비율 좌표 → 픽셀
        point_x = int(xpos * (width - 1))

        point_y = int(ypos * (height - 1))

        marker_color = hex_to_bgr(color)

        # 이슈 위치 표시
        cv2.circle(image, (point_x, point_y), 15, (255, 255, 255), -1)

        cv2.circle(image, (point_x, point_y), 10, marker_color, -1)

        # 파일명
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        filename = f"issue_shopmap_" f"{shopmapno}_" f"{timestamp}.png"

        output_path = AIISSUEMAP_DIR / filename

        # 이미지 저장
        success, encoded = cv2.imencode(".png", image)

        if not success:
            raise ValueError("이슈맵 이미지 생성 실패")

        encoded.tofile(str(output_path))

        # ------------------------------------
        # 성공 DB 저장
        # ------------------------------------

        aimapno = save_ai_issue_map(
            mno=mno,
            smno=shopmapno,
            xpos=xpos,
            ypos=ypos,
            color=color,
            fsaved=filename,
            status=1,
            err=None,
        )

        return {"success": True, "aimapno": aimapno, "status": 1, "fsaved": filename}

    except Exception as e:

        # ------------------------------------
        # 이미지 생성 실패 DB 저장
        # ------------------------------------

        aimapno = save_ai_issue_map(
            mno=mno,
            smno=shopmapno,
            xpos=xpos,
            ypos=ypos,
            color=None,
            fsaved=None,
            status=2,
            err=str(e)[:1000],
        )

        return {"success": False, "aimapno": aimapno, "status": 2, "message": str(e)}
