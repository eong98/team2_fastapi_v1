from pydantic import BaseModel, Field

# ========================================
# CCTV 이슈 알림 처리 요청
# ========================================


class NotificationIssueRequest(BaseModel):
    """
    CCTV 이슈 1건에 대한 알림 처리 요청.

    전달 정보:
    - CCTV 이슈번호
    - CCTV 이슈 상태값
    - 매장번호
    - CCTV번호
    - X / Y 좌표

    X/Y 좌표는 현재 Jetson 개발 전이므로 선택값으로 처리한다.
    """

    cino: int = Field(..., description="CCTV 이슈번호 (CCTV_ISSUE.NO)")

    state: int = Field(..., description="CCTV 이슈 현재 상태값 (CCTV_ISSUE.STATE)")

    sno: int = Field(..., description="매장번호 (SHOP.NO)")

    cno: int = Field(..., description="CCTV번호 (CCTV.NO)")

    xpos: float | None = Field(
        default=None, ge=0, le=1, description="이슈 발생 X좌표 (0~1, 현재는 선택값)"
    )

    ypos: float | None = Field(
        default=None, ge=0, le=1, description="이슈 발생 Y좌표 (0~1, 현재는 선택값)"
    )


# ========================================
# CCTV 이슈 알림 처리 응답
# ========================================


class NotificationIssueResponse(BaseModel):
    """
    CCTV 이슈 알림 처리 결과.
    """

    cino: int
    processedMembers: int
    successCount: int
    failCount: int
    message: str
