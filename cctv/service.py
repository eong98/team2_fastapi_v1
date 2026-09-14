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

# CCTV_VISITOR.STATE
VISITOR_STATE_IN = 0      # 입장중
VISITOR_STATE_OUT = 1     # 정상퇴장
VISITOR_STATE_LONG = 2    # 장시간체류


def report_issue(cno: int, code: str, detail: str, confidence: float) -> dict:
    if not is_valid_code(code):
        raise ValueError(f"등록되지 않았거나 사용 중지된 코드입니다: {code}")

    if confidence < 0 or confidence > 100:
        raise ValueError("confidence는 0~100 사이여야 합니다.")

    comnet = polish_detail_to_comnet(code, detail)
    reliability = f"{confidence:.0f}"  # 화면에서 formatReliability가 %를 붙여서 표시

    no = _insert_issue(cno=cno, code=code, comnet=comnet, reliability=reliability)

    return {
        "no": no,
        "code": code,
        "comnet": comnet,
        "reliability": reliability,
    }


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


def visitor_exit(cno: int, track_id: str, outtime: str,
                 staytime: int, state: int = VISITOR_STATE_OUT) -> dict:
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
