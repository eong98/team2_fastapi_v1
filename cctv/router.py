# -*- coding: utf-8 -*-
"""cctv/router.py - Jetson 워커가 호출하는 엔드포인트"""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from cctv.schema import (
    CctvIssueReportRequest,
    CctvIssueReportResponse,
    CctvVisitorEnterRequest,
    CctvVisitorExitRequest,
    CctvVisitorResponse,
)
from cctv.service import report_issue, dispatch_issue, visitor_enter, visitor_exit

router = APIRouter(
    prefix="/api/cctv",
    tags=["CCTV AI"],
)


@router.post("/issue/report", response_model=CctvIssueReportResponse)
def report(request: CctvIssueReportRequest, background: BackgroundTasks):
    """
    Jetson 워커가 확정한 이상행동 이벤트를 받아 CCTV_ISSUE에 저장한다.

    Jetson이 이미 code와 confidence를 확정해서 보내므로, 서버는 재판단하지 않는다:
    - code 유효성만 검증 (CCTV_ISSUE_CODE 참조)
    - detail을 LLM으로 comnet 문장만 다듬음
    - confidence를 reliability로 그대로 저장

    좌표(x, y)가 함께 오면 AI 이슈 도면 생성을 **백그라운드로** 요청한다.
    도면 생성은 LLM을 또 호출해서 느리므로, 여기서 기다리면 Jetson 응답이 그만큼 늦어진다.
    BackgroundTasks에 넘기면 응답을 먼저 보내고 서버가 뒤이어 처리한다.
    """
    try:
        result = report_issue(
            cno=request.cno,
            code=request.code,
            detail=request.detail,
            confidence=request.confidence,
            x=request.x,
            y=request.y,
        )

        # 신뢰도가 기준(NOTIFY_MIN_CONFIDENCE, 기본 60) 이상일 때만 후속 처리를 넘긴다.
        # 저장은 전부 하되, 사람을 부르는 알림은 확실한 것만 보내기 위한 게이트.
        if result.get("notify"):
            background.add_task(
                dispatch_issue,
                no=result["no"],
                cno=result["cno"],
                sno=result["sno"],
                code=result["code"],
                comnet=result["comnet"],
                confidence=request.confidence,
                x=result.get("x"),
                y=result.get("y"),
            )
        else:
            print("[cctv] 신뢰도 %.1f < 기준 - 저장만 하고 알림은 생략 (no=%s)"
                  % (request.confidence, result["no"]))

        return result

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
