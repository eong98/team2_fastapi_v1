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


class CctvIssueReportResponse(BaseModel):
    no: int
    code: str
    comnet: str
    reliability: str
