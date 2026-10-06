# -*- coding: utf-8 -*-
"""cctv/schema.py - Jetson <-> FastAPI 요청/응답 형식"""

from typing import List, Optional

from pydantic import BaseModel


class CctvIssueReportRequest(BaseModel):
    """Jetson 워커가 POST /api/cctv/issue/report 로 보내는 요청 본문."""

    cno: int                       # CCTV 번호 (CCTV_STREAM/CCTV 테이블의 CNO)
    code: str                      # Jetson이 확정한 코드 ('01'|'03'|'04' 등, CCTV_ISSUE_CODE에 등록된 값)
    detail: str                    # Jetson이 계산한 판단 근거 (comnet으로 다듬어지는 원문)
    confidence: float              # 0~100, Jetson이 계산한 신뢰도 (서버는 재판단 없이 그대로 저장)
    trackIds: List[int] = []       # 참고용 (관제 로그/디버깅), DB에는 저장하지 않음
    detectedAt: Optional[float] = None  # 참고용 (unix timestamp), 서버 저장 시각(cdate)과는 별개

    # 상황 단위 중복 억제 관련 (incident.py)
    #   'new'        : 새로 발생한 상황 (최초 1회)
    #   'escalation' : 이미 보고된 상황이 오래 지속되어 다시 알리는 것
    stage: str = "new"
    duration: float = 0.0          # 상황이 시작된 뒤 흐른 시간(초)

    # 발생 위치 (homography.json이 있을 때만 채워짐)
    imgX: Optional[float] = None   # 카메라 화면 좌표
    imgY: Optional[float] = None
    imgW: Optional[int] = None     # 기준 해상도
    imgH: Optional[int] = None
    x: Optional[float] = None      # 도면 좌표 0~1 (AIISSUEMAP용)
    y: Optional[float] = None


class CctvVisitorEnterRequest(BaseModel):
    """손님 입장 - CCTV_VISITOR INSERT (STATE=0 입장중)"""

    cno: int
    trackId: str                   # AI 추적 ID (예: 20260914142208-C1-T7)
    intime: str                    # 'YYYY-MM-DD HH:MM:SS'


class CctvVisitorExitRequest(BaseModel):
    """손님 퇴장 - CCTV_VISITOR UPDATE (OUTTIME/STAYTIME/STATE)"""

    cno: int
    trackId: str
    outtime: str                   # 'YYYY-MM-DD HH:MM:SS'
    staytime: int                  # 체류 시간(분)
    state: int = 1                 # 1=정상퇴장, 2=장시간체류


class CctvVisitorResponse(BaseModel):
    no: int
    trackId: str
    state: int


class CctvIssueReportResponse(BaseModel):
    no: int                        # CCTV_ISSUE 번호 (알림/도면 쪽에서 참조)
    code: str
    comnet: str
    reliability: str
    cno: int
    sno: Optional[int] = None      # 이 CCTV가 속한 매장 번호 (서버가 조회해서 채움)
    x: Optional[float] = None      # 도면 좌표 0~1
    y: Optional[float] = None
    notify: bool = False           # 신뢰도 기준을 넘어 알림 대상인지


# ===========================================================================
# [추가] CCTV 이슈 AI 검토 (cctv/agent) - 이용자 화면 "AI 검토" 버튼이 호출
# ===========================================================================

class CctvIssueReviewRequest(BaseModel):
    """POST /api/cctv/issue/{no}/review 요청 본문."""

    sno: Optional[int] = None      # 화면에서 선택한 매장 번호. 보내면 그 매장의 이슈인지 확인한다


class CctvIssueReviewStep(BaseModel):
    """에이전트가 조회한 기록 1건 (화면의 '조회한 근거' 목록)."""

    tool: str                      # 도구 이름 (recent_issues 등)
    label: str                     # 화면 표시용 한글 이름
    summary: str                   # 조회 결과 한 줄 요약 (LLM이 아니라 서버 코드가 만든 문장)


class CctvIssueReviewResponse(BaseModel):
    no: int                        # CCTV_ISSUE 번호
    code: str
    codeName: str
    verdict: str                   # LIKELY_TRUE | LIKELY_FALSE | UNCERTAIN
    verdictLabel: str              # 정탐 가능성 높음 | 오탐 가능성 높음 | 판단 보류
    reasons: List[str]             # 판단 근거 (최대 4개)
    recommendation: str            # 권장 조치
    steps: List[CctvIssueReviewStep] = []
    fallback: bool = False         # True면 AI가 판단을 끝내지 못해 조회 기록만 제공한 것
    elapsedMs: int = 0
