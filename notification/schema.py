from pydantic import BaseModel, Field

# ========================================
# CCTV 이슈 알림 처리 요청
# ========================================


class NotificationIssueRequest(BaseModel):
    """
    CCTV 이슈 1건에 대한 알림 처리 요청.

    전달 정보:
    - CCTV 이슈번호
    - 매장번호
    - CCTV번호
    - AI 이슈맵 번호 (도면이 없는 경우 None)
    """

    cino: int = Field(..., description="CCTV 이슈번호 (CCTV_ISSUE.NO)")

    sno: int = Field(..., description="매장번호 (SHOP.NO)")

    cno: int = Field(..., description="CCTV번호 (CCTV.NO)")

    asmno: int | None = Field(
        default=None, description="AI 이슈맵 번호 (AIISSUEMAP.NO)"
    )


# ========================================
# CCTV 이슈 알림 처리 응답
# ========================================


class NotificationIssueResponse(BaseModel):
    """CCTV 이슈 알림 처리 결과."""

    cino: int
    asmno: int | None = None
    processedMembers: int
    successCount: int
    failCount: int
    message: str
