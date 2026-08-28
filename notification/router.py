from fastapi import APIRouter, HTTPException

from notification.schema import (
    NotificationCreateRequest,
    NotificationCreateResponse,
)
from notification.service import create_notification

router = APIRouter(prefix="/api/notification", tags=["Notification AI"])


@router.post("/create", response_model=NotificationCreateResponse)
def create(request: NotificationCreateRequest):
    """AI 이슈맵 생성 후 알림 저장."""

    try:
        return create_notification(
            cino=request.cino,
            mno=request.mno,
            shopmapno=request.shopmapno,
            issue=request.issue,
            xpos=request.xpos,
            ypos=request.ypos,
            lang=request.lang,
            priority=request.priority,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"알림 생성 실패: {str(e)}")
