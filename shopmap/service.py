import socket
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from modules.issue import analyze_issue

H200_IP = "139.150.91.194"


# ========================================
# 실행 환경 확인
# ========================================


def is_h200() -> bool:
    """
    현재 실행 환경이 H200 서버인지 확인한다.
    """
    ip_address = socket.gethostbyname(socket.gethostname())

    return ip_address == H200_IP


# ========================================
# 실행 환경별 저장 경로
# ========================================

if is_h200():

    print("-> H200 Storage 사용")

    SHOPMAP_DIR = Path.home() / "allimio" / "shopmap"

    AIISSUEMAP_DIR = Path.home() / "allimio" / "aiissuemap"

else:

    print("-> Local Storage 사용")

    SHOPMAP_DIR = Path(r"C:\kd\deploy\allimio\shopmap")

    AIISSUEMAP_DIR = Path(r"C:\kd\deploy\allimio\aiissuemap")


print("SHOPMAP_DIR =", SHOPMAP_DIR)
print("AIISSUEMAP_DIR =", AIISSUEMAP_DIR)


SHOPMAP_DIR.mkdir(parents=True, exist_ok=True)

AIISSUEMAP_DIR.mkdir(parents=True, exist_ok=True)


# ========================================
# 기본 AI 도면 생성
# ========================================


def create_base_ai_map(original_path: Path, output_path: Path):
    """
    원본 도면을 단순한 흑백 선형 도면으로 변환한다.

    현재는 OpenCV 기반으로 처리한다.
    """

    image_data = np.fromfile(str(original_path), dtype=np.uint8)

    image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("원본 도면 이미지를 읽을 수 없습니다.")

    # 1. 그레이스케일 변환
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 2. 노이즈 제거
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # 3. 윤곽선 검출
    edges = cv2.Canny(blurred, 50, 150)

    # 4. 선 굵기 보정
    kernel = np.ones((2, 2), np.uint8)

    edges = cv2.dilate(edges, kernel, iterations=1)

    # 5. 흰 배경 + 검은 선
    result = cv2.bitwise_not(edges)

    # 6. 저장
    success, encoded_image = cv2.imencode(".png", result)

    if not success:
        raise ValueError("기본 AI 도면 이미지 생성에 실패했습니다.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    encoded_image.tofile(str(output_path))


# ========================================
# 원본 도면 업로드 + 기본 AI 도면 생성
# ========================================


async def process_shopmap(shopmapno: int, file):
    """
    1. 원본 도면 업로드
    2. 원본 도면 저장
    3. 기본 AI 도면 생성
    """

    if shopmapno <= 0:
        raise ValueError("유효하지 않은 매장 도면 번호입니다.")

    if file is None:
        raise ValueError("원본 매장 도면이 없습니다.")

    if not file.filename:
        raise ValueError("원본 매장 도면 파일명이 없습니다.")

    extension = Path(file.filename).suffix.lower()

    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}

    if extension not in allowed_extensions:
        raise ValueError("jpg, jpeg, png, webp 파일만 사용할 수 있습니다.")

    # 원본 도면 저장
    original_filename = f"shopmap_{shopmapno}{extension}"

    original_path = SHOPMAP_DIR / original_filename

    content = await file.read()

    if not content:
        raise ValueError("원본 도면 파일이 비어 있습니다.")

    original_path.write_bytes(content)

    # 기본 AI 도면 생성
    ai_filename = f"ai_shopmap_{shopmapno}.png"

    ai_output_path = AIISSUEMAP_DIR / ai_filename

    create_base_ai_map(original_path=original_path, output_path=ai_output_path)

    return {
        "success": True,
        "message": "기본 AI 도면 생성 완료",
        "shopmapno": shopmapno,
        "originalFile": original_filename,
        "aiFile": ai_filename,
        "status": "generated",
    }


# ========================================
# HEX → OpenCV BGR
# ========================================


def hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    """
    #RRGGBB 형식의 HEX 색상을
    OpenCV BGR 색상으로 변환한다.
    """

    hex_color = hex_color.strip().lstrip("#")

    if len(hex_color) != 6:
        raise ValueError("색상값 형식이 올바르지 않습니다.")

    red = int(hex_color[0:2], 16)

    green = int(hex_color[2:4], 16)

    blue = int(hex_color[4:6], 16)

    return (blue, green, red)


# ========================================
# AI 이슈 도면 생성
# ========================================


def create_issue_map(shopmapno: int, issue: str, xpos: float, ypos: float):
    """
    1. 기본 AI 도면 존재 확인
    2. AI가 이슈 분석
    3. AI가 유형 / 위험도 / 색상 결정
    4. 지정 위치에 마커 표시
    5. 최종 이슈 도면 저장
    """

    if shopmapno <= 0:
        raise ValueError("유효하지 않은 매장 도면 번호입니다.")

    if issue is None or not issue.strip():
        raise ValueError("이슈 내용이 없습니다.")

    # 좌표 검사
    if not 0 <= xpos <= 1:
        raise ValueError("xpos는 0~1 사이 값이어야 합니다.")

    if not 0 <= ypos <= 1:
        raise ValueError("ypos는 0~1 사이 값이어야 합니다.")

    # 기본 AI 도면 확인
    base_filename = f"ai_shopmap_{shopmapno}.png"

    base_path = AIISSUEMAP_DIR / base_filename

    if not base_path.exists():
        raise ValueError("기본 AI 도면이 없습니다. " "먼저 AI 도면을 생성해주세요.")

    # AI 이슈 분석
    ai_result = analyze_issue(issue)

    issue_type = ai_result["issueType"]
    severity = ai_result["severity"]
    color = ai_result["color"]
    reason = ai_result["reason"]

    # 기본 AI 도면 읽기
    image_data = np.fromfile(str(base_path), dtype=np.uint8)

    image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("기본 AI 도면을 읽을 수 없습니다.")

    height, width = image.shape[:2]

    # 비율 좌표 → 픽셀 좌표
    point_x = int(xpos * (width - 1))

    point_y = int(ypos * (height - 1))

    # AI 색상 변환
    marker_color = hex_to_bgr(color)

    # 흰색 테두리
    cv2.circle(image, (point_x, point_y), 15, (255, 255, 255), -1)

    # AI가 판단한 색상
    cv2.circle(image, (point_x, point_y), 10, marker_color, -1)

    # 중앙 표시
    cv2.circle(image, (point_x, point_y), 3, (0, 0, 0), -1)

    # 최종 파일명
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    issue_filename = f"issue_shopmap_" f"{shopmapno}_" f"{timestamp}.png"

    issue_path = AIISSUEMAP_DIR / issue_filename

    # 최종 도면 저장
    success, encoded_image = cv2.imencode(".png", image)

    if not success:
        raise ValueError("AI 이슈 도면 이미지 생성에 실패했습니다.")

    encoded_image.tofile(str(issue_path))

    return {
        "success": True,
        "message": "AI 이슈 도면 생성 완료",
        "shopmapno": shopmapno,
        "issue": issue,
        "issueType": issue_type,
        "severity": severity,
        "color": color,
        "reason": reason,
        "xpos": xpos,
        "ypos": ypos,
        "pixelX": point_x,
        "pixelY": point_y,
        "fsaved": issue_filename,
        "status": "generated",
    }
