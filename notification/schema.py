from pydantic import BaseModel, Field


class NotificationCreateRequest(BaseModel):
    cino: int = Field(..., description="CCTV 이슈 번호")
    mno: int = Field(..., description="알림 수신 회원 번호")
    shopmapno: int = Field(..., description="매장 도면 번호")
    issue: str = Field(..., description="알림에 사용할 이슈 내용")
    xpos: float | None = None
    ypos: float | None = None
    lang: str | None = Field(default=None, description="번역 언어 예: en, ja")
    priority: str = Field(default="NORMAL", description="LOW/NORMAL/HIGH/EMERGENCY")


class NotificationCreateResponse(BaseModel):
    no: int
    cino: int
    mno: int
    aimapno: int | None = None
    status: str
    mapStatus: int
    translated: bool
