from fastapi import APIRouter, HTTPException

from cctv.schema import (
    IssueDetectRequest,
    VisitorEnterRequest,
    VisitorExitRequest,
)
from cctv.service import (
    LOITER_THRESHOLD_MINUTES,
    CODE_LOITER,
    create_issue,
    has_recent_open_issue,
    visitor_enter,
    visitor_exit,
)
from core.codes_cache import get_code_map, is_valid_code
from modules.cctv_issue import analyze_cctv_issue


router = APIRouter(
    prefix="/api/cctv",
    tags=["CCTV AI"]
)


# ========================================
# 손님(방문객) 입/퇴장 - Jetson 트래커가 호출
# ========================================

@router.post("/visitor/enter")
def enter_visitor(request: VisitorEnterRequest):
    """
    새 track_id가 프레임에 처음 등장했을 때 호출. CCTV_VISITOR에 입장 레코드를 만든다.
    """
    try:
        return visitor_enter(cno=request.cno, track_id=request.track_id)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"손님 입장 기록 실패: {str(e)}"
        )


@router.post("/visitor/exit")
def exit_visitor(request: VisitorExitRequest):
    """
    track_id가 일정 시간(예: 10초) 이상 프레임에서 사라졌을 때 호출.
    CCTV_VISITOR를 퇴장 처리하고, 장시간체류로 판정되면 CCTV_ISSUE도 함께 생성한다.

    (장시간체류는 '영상 판단'이 아니라 '입장~퇴장 시각 계산'이라 서버가 계산해도
    문제없다. Jetson AI 판단이 필요한 폭행/쓰러짐 등과는 성격이 다르다.)
    """
    try:
        result = visitor_exit(cno=request.cno, track_id=request.track_id)

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="퇴장 처리할 입장 기록을 찾을 수 없습니다."
            )

        if result["loiterTriggered"]:
            comnet = (
                f"동일 인물(track_id={request.track_id})이 "
                f"{result['staytime']}분 동안 체류했습니다. "
                f"(기준: {LOITER_THRESHOLD_MINUTES}분 이상)"
            )

            create_issue(
                cno=request.cno,
                code=CODE_LOITER,
                comnet=comnet,
                reliability=100,
            )

        return result

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"손님 퇴장 처리 실패: {str(e)}"
        )


# ========================================
# 이상행동 이슈 - Jetson이 유형(code)까지 확정, 서버는 문장만 다듬어서 저장
# ========================================

@router.post("/issue/detect")
def detect_issue(request: IssueDetectRequest):
    """
    Jetson이 영상을 직접 보고 확정한 이벤트(code + detail + confidence)를 받아서
    CCTV_ISSUE에 저장한다.

    서버는 code를 다시 판단하지 않는다(영상을 볼 수 없어서 재검증이 불가능하기 때문).
    서버가 하는 일은 두 가지뿐이다.
      1) code가 유효한 값인지 검증 (modules.cctv_issue.analyze_cctv_issue)
      2) detail을 관리자가 읽기 좋은 문장(comnet)으로 다듬기 (LLM)

    ⚠️ LLM 문장 다듬기가 실패해도(Ollama 다운 등) 이슈 저장 자체는 막지 않는다.
       안전 이벤트라서 "문장이 안 예뻐서 저장 실패"가 있으면 안 되기 때문에,
       실패 시 Jetson이 보낸 detail을 그대로 comnet으로 써서 저장한다.
    """
    if not is_valid_code(request.code):
        raise HTTPException(
            status_code=400,
            detail=f"code 값이 올바르지 않습니다: {request.code!r} (허용값: {list(get_code_map())})"
        )

    try:
        ai_result = analyze_cctv_issue(
            code=request.code,
            detail=request.detail,
            confidence=request.confidence,
        )
        code = ai_result["code"]
        comnet = ai_result["comnet"]
        reliability = ai_result["reliability"]

    except Exception as e:
        # LLM 문장 다듬기 실패 - Jetson이 보낸 값을 그대로 써서 저장은 계속 진행한다.
        print(f"[cctv/issue/detect] comnet 생성 실패, detail을 그대로 사용: {e}")
        code = request.code
        comnet = request.detail
        reliability = round(request.confidence * 100)

    if has_recent_open_issue(cno=request.cno, code=code):
        return {
            "skipped": True,
            "reason": "동일 유형의 최근 이슈가 이미 존재합니다.",
            "code": code,
        }

    try:
        result = create_issue(
            cno=request.cno,
            code=code,
            comnet=comnet,
            reliability=reliability,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"CCTV 이슈 저장 실패: {str(e)}"
        )

    result["skipped"] = False

    return result
