# -*- coding: utf-8 -*-
"""cctv/router.py - Jetson 워커가 호출하는 엔드포인트"""

from fastapi import APIRouter, HTTPException

from cctv.schema import CctvIssueReportRequest, CctvIssueReportResponse
from cctv.service import report_issue

router = APIRouter(
    prefix="/api/cctv",
    tags=["CCTV AI"],
)


@router.post("/issue/report", response_model=CctvIssueReportResponse)
def report(request: CctvIssueReportRequest):
    """
    Jetson 워커가 확정한 이상행동 이벤트(01/03/04 등)를 받아 CCTV_ISSUE에 저장한다.

    Jetson이 이미 code와 confidence를 확정해서 보내므로, 서버는 재판단하지 않는다:
    - code 유효성만 검증 (CCTV_ISSUE_CODE 참조)
    - detail을 LLM으로 comnet 문장만 다듬음
    - confidence를 reliability로 그대로 저장
    """
    try:
        return report_issue(
            cno=request.cno,
            code=request.code,
            detail=request.detail,
            confidence=request.confidence,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CCTV 이슈 저장 실패: {str(e)}")
