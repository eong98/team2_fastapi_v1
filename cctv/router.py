# -*- coding: utf-8 -*-
"""cctv/router.py - Jetson 워커가 호출하는 엔드포인트"""

from fastapi import APIRouter, HTTPException

from cctv.schema import (
    CctvIssueReportRequest,
    CctvIssueReportResponse,
    CctvVisitorEnterRequest,
    CctvVisitorExitRequest,
    CctvVisitorResponse,
)
from cctv.service import report_issue, visitor_enter, visitor_exit

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


# ===========================================================================
# 손님(방문객) 입·퇴장
# ===========================================================================

@router.post("/visitor/enter", response_model=CctvVisitorResponse)
def enter(request: CctvVisitorEnterRequest):
    """손님이 매장에 들어온 것을 CCTV_VISITOR에 기록한다(STATE=0 입장중)."""
    try:
        return visitor_enter(
            cno=request.cno,
            track_id=request.trackId,
            intime=request.intime,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"손님 입장 저장 실패: {str(e)}")


@router.post("/visitor/exit", response_model=CctvVisitorResponse)
def exit_(request: CctvVisitorExitRequest):
    """손님이 나간 것을 기록한다. 입장 행에 OUTTIME/STAYTIME/STATE를 채운다."""
    try:
        return visitor_exit(
            cno=request.cno,
            track_id=request.trackId,
            outtime=request.outtime,
            staytime=request.staytime,
            state=request.state,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"손님 퇴장 저장 실패: {str(e)}")
