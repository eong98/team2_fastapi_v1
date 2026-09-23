# -*- coding: utf-8 -*-
"""
cctv/service.py

Jetson이 확정한 이상행동 이벤트를 받아서:
1. code가 CCTV_ISSUE_CODE에 등록된 유효한 값인지 검증
2. LLM으로 detail -> comnet(관리자용 한국어 문장) 다듬기 (실패해도 저장은 계속 진행)
3. CCTV_ISSUE에 직접 INSERT (Spring REST API를 거치지 않음 - survey/service.py와 동일한 패턴)

PK(NO)는 Spring(JPA)이 쓰는 시퀀스 SEQ_CCTV_ISSUE_NO를 그대로 재사용한다.
"""

from datetime import datetime

from core.codes_cache import is_valid_code
from core.database import get_connection
from modules.cctv_issue import polish_detail_to_comnet

# 알림을 보낼 최소 신뢰도. 이보다 낮으면 CCTV_ISSUE에는 저장하되 알림은 호출하지 않는다.
#
# 왜 저장은 하고 알림만 막는가: 낮은 신뢰도 이벤트도 나중에 튜닝할 때 근거 자료가 되고,
# 관리자 화면에서 "이런 것도 잡혔다"를 확인할 수 있어야 한다. 다만 문자/메일로 사람을
# 부르는 건 확실한 것만 해야 하므로 여기서 가른다.
NOTIFY_MIN_CONFIDENCE = 60.0

# CCTV_VISITOR.STATE
VISITOR_STATE_IN = 0  # 입장중
VISITOR_STATE_OUT = 1  # 정상퇴장
VISITOR_STATE_LONG = 2  # 장시간체류


def report_issue(
    cno: int,
    code: str,
    detail: str,
    confidence: float,
    x: float = None,
    y: float = None,
) -> dict:
    """Jetson이 확정한 이상행동 이벤트를 CCTV_ISSUE에 저장한다.

    x, y는 Jetson이 호모그래피로 변환한 도면 좌표(0~1 비율)다. CCTV_ISSUE에는 저장하지 않고
    (좌표 컬럼이 없다) AI 이슈 도면(AIISSUEMAP) 생성에 넘겨주는 값이다.
    """
    if not is_valid_code(code):
        raise ValueError(f"등록되지 않았거나 사용 중지된 코드입니다: {code}")

    if confidence < 0 or confidence > 100:
        raise ValueError("confidence는 0~100 사이여야 합니다.")

    comnet = polish_detail_to_comnet(code, detail)
    reliability = f"{confidence:.0f}"  # 화면에서 formatReliability가 %를 붙여서 표시

    no = _insert_issue(cno=cno, code=code, comnet=comnet, reliability=reliability)

    # 이 CCTV가 어느 매장인지. 알림/도면 쪽에서 필요하다(Jetson은 cno만 안다).
    sno = find_shop_no(cno)

    return {
        "no": no,
        "code": code,
        "comnet": comnet,
        "reliability": reliability,
        "cno": cno,
        "sno": sno,
        "x": x,
        "y": y,
        # 신뢰도 기준을 넘겼는지 - 라우터가 알림 호출 여부를 이 값으로 판단한다
        "notify": confidence >= NOTIFY_MIN_CONFIDENCE,
    }


def find_shop_no(cno: int):
    """CCTV 번호로 그 CCTV가 속한 매장 번호(SHOP.NO = CCTV.SNO)를 찾는다.

    Jetson은 자기가 담당하는 CCTV 번호만 알기 때문에, 매장 단위로 동작하는
    알림/도면 쪽에 넘겨주려면 서버에서 한 번 조회해줘야 한다.
    컬럼명이 다르면 이 쿼리만 고치면 된다. 못 찾으면 None.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT SNO FROM CCTV WHERE no = :cno", cno=cno)
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    except Exception as e:
        print(f"[cctv] 매장번호 조회 실패 (cno={cno}): {e}")
        return None

    finally:
        cursor.close()
        connection.close()


# ===========================================================================
# AI 이슈 도면 연동 (AIISSUEMAP) - 좌표를 넘겨주는 부분
# ===========================================================================
#
# [역할 경계]
# 이 함수는 "이슈가 발생했으니 이 좌표로 도면을 만들어 달라"고 호출해주는 것까지만 담당한다.
# 실제 도면 생성/저장(shopmap 쪽)은 담당자가 별도로 구현/수정한다.
#
# 주의 1. shopmap.service.create_issue_map()은 내부에서 LLM(analyze_issue)을 호출해 느리다.
#         그래서 라우터에서 BackgroundTasks로 호출해야 한다 - 직접 부르면 Jetson 응답이 늦어진다.
# 주의 2. create_issue_map()은 xpos/ypos를 0~1로만 받는다. Jetson이 이미 정규화해서 보낸다.
# 주의 3. 요청에 필요한 shopmapno(원본 매장 도면 번호)를 Jetson은 모른다(cno만 안다).
#         아래 _find_shopmapno()가 cno -> shopmapno 조회를 담당하는데, 테이블 관계가 확정되면
#         쿼리를 맞춰야 한다. 조회 실패 시 도면 생성만 건너뛰고 이슈 저장은 그대로 유지한다.

def find_shopmapno(cno: int):
    """CCTV 번호로 그 매장의 원본 도면 번호(SHOPMAP.NO)를 찾는다.

    경로: CCTV.CNO -> CCTV.SNO(매장) -> SHOPMAP.SNO -> SHOPMAP.NO
    테이블/컬럼명이 다르면 이 쿼리만 고치면 된다. 못 찾으면 None을 반환한다.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT sm.NO
              FROM SHOPMAP sm
              JOIN CCTV c ON c.SNO = sm.SNO
             WHERE c.NO = :cno
             ORDER BY sm.NO DESC
             FETCH FIRST 1 ROWS ONLY
            """,
            cno=cno,
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None

    except Exception as e:
        # 테이블 구조가 아직 확정되지 않았을 수 있다. 이슈 저장을 막지 않도록 조용히 넘어간다.
        print(f"[cctv] shopmapno 조회 실패 (cno={cno}): {e}")
        return None

    finally:
        cursor.close()
        connection.close()


def dispatch_issue(no: int, cno: int, sno, code: str, comnet: str,
                   confidence: float, x=None, y=None) -> dict:
    """이슈가 확정되어 저장된 뒤, 알림/도면 담당 쪽에 넘겨주는 단일 호출 지점.

    라우터가 BackgroundTasks로 호출한다(아래 작업들이 LLM을 또 부르기 때문에 느림).

    [역할 경계]
    여기까지가 CCTV(장우원) 담당이다. 이 함수는 "이슈가 났고, 값은 이거다"를 넘겨주는 것까지만
    하고, 실제 알림 발송과 도면 생성은 담당자가 구현/수정한다. 필요한 값이 더 있으면
    아래 payload에 추가하면 된다.
f
    넘기는 값:
      no         - CCTV_ISSUE 번호 (방금 저장된 이슈의 PK)
      cno        - CCTV 번호
      sno        - 매장 번호
      code       - 이상행동 코드 ('01'~'06')
      comnet     - 관리자용 한국어 설명 (LLM이 다듬은 문장)
      confidence - 신뢰도 0~100
      x, y       - 도면 좌표 0~1 비율 (호모그래피 결과, 없으면 None)

    실패해도 예외를 밖으로 던지지 않는다 - 이미 CCTV_ISSUE 저장은 끝났고,
    후속 처리 실패가 안전 이벤트 기록을 되돌릴 이유는 없다.
    """
    payload = {
        "no": no,
        "cno": cno,
        "sno": sno,
        "code": code,
        "comnet": comnet,
        "confidence": confidence,
        "x": x,
        "y": y,
    }
    print(f"[cctv] 이슈 후속처리 요청: {payload}")

    result = {"payload": payload}

    # --- (1) AI 이슈 도면 생성 (AIISSUEMAP) ---
    result["issueMap"] = _call_issue_map(no=no, code=code, sno=sno, x=x, y=y)

    # 생성된 AIISSUEMAP.NO를 알림에 전달
    # 도면이 없거나 생성되지 않은 경우 None
    payload["asmno"] = result["issueMap"].get("aimapno")

    # --- (2) 알림 발송 (문자/메일) ---
    result["notify"] = _call_notify(payload)

    return result


def _call_issue_map(no: int, code: str, sno: int, x, y) -> dict:
    """CCTV 이슈 정보를 AIISSUEMAP으로 전달하여 이슈 위치가 표시된 도면을 생성한다."""

    # 좌표가 없으면 도면 생성을 하지 않음
    if x is None or y is None:
        return {"skipped": "좌표 없음 (homography.json 미설정)"}

    # x, y는 도면 기준 0~1 비율 좌표만 허용
    if not (0 <= x <= 1 and 0 <= y <= 1):
        return {"skipped": f"좌표가 0~1 범위를 벗어남: ({x}, {y})"}

    try:
        # AIISSUEMAP 담당 모듈 호출
        # CCTV에서는 no/code/sno/x/y만 전달하고, 도면 조회와 색상 결정은 AIISSUEMAP에서 처리
        from aiissuemap.service import process_cctv_issue_map

        out = process_cctv_issue_map(no=no, code=code, sno=sno, x=x, y=y)

        print(f"[cctv] AI 이슈 도면 처리 완료 (no={no}, code={code}, sno={sno})")
        return out

    except ImportError:
        # AIISSUEMAP 모듈을 불러오지 못해도 CCTV 기능 전체가 중단되지 않도록 처리
        return {"skipped": "aiissuemap 모듈 없음"}

    except Exception as e:
        # AIISSUEMAP 처리 중 오류가 발생해도 CCTV 이슈 저장 자체는 유지
        print(f"[cctv] AI 이슈 도면 처리 실패 (no={no}, code={code}, sno={sno}): {e}")
        return {"success": False, "aimapno": None, "fsaved": None, "message": str(e)}


def _call_notify(payload: dict) -> dict:
    """CCTV 이슈 정보를 Notification으로 전달하여 문자/메일 알림을 처리한다."""

    try:
        from notification.service import process_cctv_issue

        return process_cctv_issue(
            cino=payload["no"],
            sno=payload["sno"],
            cno=payload["cno"],
            asmno=payload.get("asmno"),
        )

    except ImportError:
        return {"skipped": "notification 모듈을 불러올 수 없습니다."}

    except Exception as e:
        print(
            f"[cctv] 알림 발송 실패 "
            f"(cino={payload['no']}, cno={payload['cno']}): {e}"
        )

        return {
            "success": False,
            "error": str(e),
        }


# ===========================================================================
# CCTV_ISSUE INSERT
# ===========================================================================


def _insert_issue(cno: int, code: str, comnet: str, reliability: str) -> int:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO CCTV_ISSUE (
                NO, CNO, MNO, CODE, STATE, COMNET, RELIABILITY, PDATE, NOTICEYN, CDATE
            ) VALUES (
                SEQ_CCTV_ISSUE_NO.NEXTVAL, :cno, NULL, :code, 0, :comnet, :reliability, NULL, 'N', :cdate
            )
            """,
            cno=cno,
            code=code,
            comnet=comnet,
            reliability=reliability,
            cdate=now,
        )

        cursor.execute("SELECT SEQ_CCTV_ISSUE_NO.CURRVAL FROM DUAL")
        inserted_no = cursor.fetchone()[0]

        connection.commit()
        return int(inserted_no)

    finally:
        cursor.close()
        connection.close()


# ===========================================================================
# 손님(방문객) 입·퇴장 - CCTV_VISITOR
# ===========================================================================


def visitor_enter(cno: int, track_id: str, intime: str) -> dict:
    """손님 입장. CCTV_VISITOR에 STATE=0(입장중)으로 INSERT한다.

    같은 trackId가 이미 있으면 중복 INSERT하지 않고 기존 행을 돌려준다
    (젯슨이 네트워크 오류로 재전송하는 경우 대비 - 멱등성 확보).
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            "SELECT NO FROM CCTV_VISITOR WHERE TRACK_ID = :track_id",
            track_id=track_id,
        )
        row = cursor.fetchone()
        if row:
            return {"no": int(row[0]), "trackId": track_id, "state": VISITOR_STATE_IN}

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO CCTV_VISITOR (
                NO, CNO, TRACK_ID, INTIME, OUTTIME, STAYTIME, STATE, CDATE
            ) VALUES (
                SEQ_CCTV_VISITOR_NO.NEXTVAL, :cno, :track_id, :intime, NULL, NULL, :state, :cdate
            )
            """,
            cno=cno,
            track_id=track_id,
            intime=intime,
            state=VISITOR_STATE_IN,
            cdate=now,
        )

        cursor.execute("SELECT SEQ_CCTV_VISITOR_NO.CURRVAL FROM DUAL")
        inserted_no = int(cursor.fetchone()[0])

        connection.commit()
        return {"no": inserted_no, "trackId": track_id, "state": VISITOR_STATE_IN}

    finally:
        cursor.close()
        connection.close()


def visitor_exit(
    cno: int, track_id: str, outtime: str, staytime: int, state: int = VISITOR_STATE_OUT
) -> dict:
    """손님 퇴장. 입장 때 만들어진 행을 찾아 OUTTIME/STAYTIME/STATE를 채운다.

    이미 퇴장 처리된 행은 다시 건드리지 않는다(STATE=0 조건) - 재전송이 와도 안전하다.
    """
    if state not in (VISITOR_STATE_OUT, VISITOR_STATE_LONG):
        raise ValueError(f"state는 1(정상퇴장) 또는 2(장시간체류)여야 합니다: {state}")

    if staytime < 0:
        raise ValueError("staytime은 0 이상이어야 합니다.")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE CCTV_VISITOR
               SET OUTTIME = :outtime,
                   STAYTIME = :staytime,
                   STATE = :state
             WHERE TRACK_ID = :track_id
               AND STATE = :state_in
            """,
            outtime=outtime,
            staytime=staytime,
            state=state,
            track_id=track_id,
            state_in=VISITOR_STATE_IN,
        )
        updated = cursor.rowcount
        connection.commit()

        if updated == 0:
            # 입장 기록이 없거나 이미 퇴장 처리됨. 워커는 계속 돌아야 하므로 예외를 던지지 않는다.
            return {"no": 0, "trackId": track_id, "state": state}

        cursor.execute(
            "SELECT NO FROM CCTV_VISITOR WHERE TRACK_ID = :track_id",
            track_id=track_id,
        )
        row = cursor.fetchone()
        return {"no": int(row[0]) if row else 0, "trackId": track_id, "state": state}

    finally:
        cursor.close()
        connection.close()
