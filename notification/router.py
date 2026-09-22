from fastapi import APIRouter, HTTPException

from notification.schema import NotificationIssueRequest
from notification.service import process_cctv_issue

router = APIRouter(
    prefix="/api/notification",
    tags=["Notification"],
)


# ========================================
# CCTV 이슈 알림 처리
# ========================================


@router.post("/issue")
def process_issue_notification(
    request: NotificationIssueRequest,
):
    """
    CCTV 이슈 1건의 알림을 처리한다.

    AI 이슈맵은 CCTV 후속 처리 단계에서 이미 생성되며,
    Notification에서는 전달받은 ASMNO를 사용한다.
    """

    try:
        return process_cctv_issue(
            cino=request.cino,
            sno=request.sno,
            cno=request.cno,
            asmno=request.asmno,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"알림 처리 실패: {str(e)}",
        )
