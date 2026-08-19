from pydantic import BaseModel, Field


# ========================================
# CCTV_VISITOR - 손님(방문객) 입/퇴장
# ========================================

class VisitorEnterRequest(BaseModel):
    """
    Jetson이 새로운 track_id를 처음 발견했을 때 호출.
    """
    cno: int = Field(..., description="CCTV번호 (CCTV.no)")
    track_id: str = Field(..., description="AI 추적 ID (예: yolo track id)")


class VisitorExitRequest(BaseModel):
    """
    Jetson이 특정 track_id를 일정 시간 이상 놓쳤을 때(퇴장 처리) 호출.
    """
    cno: int
    track_id: str


class VisitorEventResponse(BaseModel):
    no: int
    cno: int
    trackId: str
    intime: str
    outtime: str | None = None
    staytime: int | None = None
    state: int
    loiterTriggered: bool = False


# ========================================
# CCTV_ISSUE - 이상행동 이슈
# ========================================

class IssueDetectRequest(BaseModel):
    """
    ⚠️ Jetson이 "무슨 유형인지"까지 확정해서 보낸다.

    영상을 실제로 보는 건 Jetson뿐이고, 서버 LLM은 텍스트만 읽을 수 있어서
    영상 기반 판단(폭행/쓰러짐 등 유형 확정)을 서버가 대신 내릴 수 없다.
    그래서 CODE는 Jetson이 자기 모델/휴리스틱으로 이미 확정한 값을 그대로 받고,
    서버는 그 CODE를 다시 판단하지 않는다(=재분류 금지).

    서버가 하는 일은 딱 두 가지뿐이다.
      1) code가 유효한 값인지 검증
      2) detail(탐지 근거)을 관리자가 읽기 좋은 한국어 문장(comnet)으로 다듬기
    """
    cno: int = Field(..., description="CCTV번호")
    code: str = Field(
        ...,
        description="Jetson이 확정한 문제유형코드 (01=폭행, 02=기물파손, 03=쓰러짐/응급, "
                    "04=무단침입, 05=장시간체류). 서버는 이 값을 재분류하지 않는다.",
    )
    detail: str = Field(
        ...,
        description="탐지 근거를 사람이 읽을 수 있는 문장/데이터로 요약한 텍스트 "
                    "(예: 'CAM 3 구역에서 두 사람의 바운딩박스 중심 거리 42px, 0.3초 유지')",
    )
    confidence: float = Field(
        ..., ge=0, le=1,
        description="Jetson이 자체 계산한 최종 신뢰도 (0~1). "
                     "서버는 이 값을 그대로 신뢰도(reliability)로 사용하거나 참고용으로만 쓴다.",
    )
    track_ids: list[str] = Field(default_factory=list, description="관련된 track_id 목록(있으면)")


class IssueResponse(BaseModel):
    no: int
    cno: int
    code: str
    state: int
    comnet: str
    reliability: str
    noticeyn: str
    cdate: str
