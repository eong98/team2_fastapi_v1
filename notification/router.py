from fastapi import APIRouter, HTTPException

from notification.schema import NotificationIssueRequest
from notification.service import process_cctv_issue

router = APIRouter(prefix="/api/notification", tags=["Notification"])


# ========================================
# CCTV 이슈 알림 처리
# ========================================


@router.post("/issue")
def process_issue_notification(request: NotificationIssueRequest):
    """
    CCTV 이슈 1건을 받아 알림 전체 처리를 시작한다.

    전달 정보:
    - CCTV 이슈번호
    - CCTV 이슈 상태값
    - 매장번호
    - CCTV번호
    - X / Y 좌표
    """

    try:
        return process_cctv_issue(
            cino=request.cino,
            state=request.state,
            sno=request.sno,
            cno=request.cno,
            xpos=request.xpos,
            ypos=request.ypos,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"알림 처리 실패: {str(e)}")
