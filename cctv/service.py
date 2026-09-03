"""
CCTV_ISSUE / CCTV_VISITOR 실제 DB 처리 로직.

survey/service.py와 동일한 패턴: core.database.get_connection()으로 Oracle에 직접 연결해서
INSERT/UPDATE 한다. PK는 Spring(JPA)이 이미 쓰고 있는 시퀀스를 그대로 재사용해야
NO 값이 서로 충돌하지 않는다.

    CCTV_ISSUE.NO   <- SEQ_CCTV_ISSUE_NO.NEXTVAL
    CCTV_VISITOR.NO <- SEQ_CCTV_VISITOR_NO.NEXTVAL

(Spring 쪽 CctvIssue.java / CctvVisitor.java의 @SequenceGenerator 시퀀스명과 반드시 맞춰야 함.
 실제 시퀀스명이 다르면 아래 두 상수만 고치면 된다.)
"""

from datetime import datetime

from core.codes_cache import is_valid_code
from core.database import get_connection

SEQ_CCTV_ISSUE = "SEQ_CCTV_ISSUE_NO"
SEQ_CCTV_VISITOR = "SEQ_CCTV_VISITOR_NO"

# 손님이 이 시간(분) 이상 머무르면 CCTV_VISITOR.STATE = 2(장시간체류)로 표시하고
# CCTV_ISSUE에도 "장시간체류" 이슈를 하나 만든다. (필요에 맞게 조정)
LOITER_THRESHOLD_MINUTES = 30

# 장시간체류는 Jetson이 아니라 서버(visitor_exit)가 직접 판정하는 유일한 코드라서
# (design 문서 "예외" 항목) 다른 코드처럼 Jetson이 값을 보내주지 않는다 - 그래서
# 이것만 여기 상수로 남겨뒀다. CCTV_ISSUE_CODE 테이블의 "05"(장시간체류) 행과
# 반드시 같은 값이어야 하며, create_issue()가 저장 시점에 CCTV_ISSUE_CODE 기준으로
# 유효성을 한 번 더 검증한다(core/codes_cache.py). 나머지 01~04(폭행/기물파손/
# 쓰러짐/무단침입)는 전부 Jetson이 보내주므로 여기 따로 상수를 두지 않는다
# (예전엔 CODE_ASSAULT/CODE_DAMAGE/CODE_FALL/CODE_INTRUSION으로 중복 정의돼 있었음).
CODE_LOITER = "05"       # 장시간체류


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ========================================
# CCTV_VISITOR
# ========================================

def visitor_enter(cno: int, track_id: str) -> dict:
    """
    새 손님(track_id) 입장 기록 생성.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        intime = _now()

        cursor.execute(
            f"""
            INSERT INTO CCTV_VISITOR (
                NO, CNO, TRACK_ID, INTIME, STATE, CDATE
            ) VALUES (
                {SEQ_CCTV_VISITOR}.NEXTVAL, :cno, :track_id, :intime, 0, :cdate
            )
            """,
            cno=cno,
            track_id=track_id,
            intime=intime,
            cdate=intime,
        )

        connection.commit()

        return {
            "cno": cno,
            "trackId": track_id,
            "intime": intime,
            "state": 0,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


def visitor_exit(cno: int, track_id: str) -> dict | None:
    """
    아직 퇴장 처리가 안 된(OUTTIME IS NULL) 가장 최근 세션을 찾아 퇴장 처리한다.
    STAYTIME(분)을 계산하고, LOITER_THRESHOLD_MINUTES를 넘으면 STATE=2, 아니면 STATE=1.

    반환값의 loiterTriggered=True면 상위 라우터에서 create_issue(code=LOITER)를
    같이 호출해줘야 한다는 뜻.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT NO, INTIME
            FROM CCTV_VISITOR
            WHERE CNO = :cno
              AND TRACK_ID = :track_id
              AND OUTTIME IS NULL
            ORDER BY NO DESC
            FETCH FIRST 1 ROWS ONLY
            """,
            cno=cno,
            track_id=track_id,
        )

        row = cursor.fetchone()

        if row is None:
            # 입장 기록 없이 퇴장 이벤트만 들어온 경우 (프레임 유실 등) - 무시
            return None

        visitor_no, intime_str = row

        intime = datetime.strptime(intime_str, "%Y-%m-%d %H:%M:%S")
        outtime = datetime.now()

        staytime_minutes = max(0, int((outtime - intime).total_seconds() // 60))

        loiter_triggered = staytime_minutes >= LOITER_THRESHOLD_MINUTES
        state = 2 if loiter_triggered else 1

        outtime_str = outtime.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            UPDATE CCTV_VISITOR
            SET OUTTIME = :outtime,
                STAYTIME = :staytime,
                STATE = :state
            WHERE NO = :no
            """,
            outtime=outtime_str,
            staytime=staytime_minutes,
            state=state,
            no=visitor_no,
        )

        connection.commit()

        return {
            "no": visitor_no,
            "cno": cno,
            "trackId": track_id,
            "outtime": outtime_str,
            "staytime": staytime_minutes,
            "state": state,
            "loiterTriggered": loiter_triggered,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


# ========================================
# CCTV_ISSUE
# ========================================

def has_recent_open_issue(cno: int, code: str, minutes: int = 5) -> bool:
    """
    같은 CCTV, 같은 유형(code)의 이슈가 최근 N분 안에 이미 등록됐는지 확인한다.
    프레임마다 계속 감지되는 걸 매번 새 이슈로 만들지 않기 위한 중복 방지용.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM CCTV_ISSUE
            WHERE CNO = :cno
              AND CODE = :code
              AND CDATE >= TO_CHAR(SYSDATE - (:minutes / 1440), 'YYYY-MM-DD HH24:MI:SS')
            """,
            cno=cno,
            code=code,
            minutes=minutes,
        )

        count = cursor.fetchone()[0]

        return count > 0

    finally:
        cursor.close()
        connection.close()


def create_issue(cno: int, code: str, comnet: str, reliability: float) -> dict:
    """
    CCTV_ISSUE 신규 등록. STATE는 항상 0(미확인)으로 시작 - 오탐/정탐 처리는
    관리자가 대시보드(PUT /cctv_issue/update, Spring)에서 한다.
    NOTICEYN은 'N'으로 두고, 알림 발송 쪽(이은혜 담당 모듈)이 폴링해서 'Y'로 갱신하는 걸 전제로 함.

    code는 이미 상위(router.py: modules.cctv_issue.analyze_cctv_issue, 또는 visitor_exit의
    CODE_LOITER)에서 한 번 검증된 값이 들어오는 게 정상이지만, CCTV_ISSUE_CODE 자체가
    실제 참조 무결성 제약(FK)까지는 아직 안 걸려있을 수 있어서 여기서도 한 번 더 막는다
    (안전 이벤트라 저장을 완전히 막기보다는 경고만 남기고 계속 진행한다).
    """
    if not is_valid_code(code):
        print(f"[cctv/service.create_issue] 경고: CCTV_ISSUE_CODE에 없는 code={code!r} 저장 시도 (cno={cno})")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cdate = _now()
        reliability_str = str(int(round(reliability)))  # VARCHAR2(3) - 0~100

        cursor.execute(
            f"""
            INSERT INTO CCTV_ISSUE (
                NO, CNO, MNO, CODE, STATE, COMNET, RELIABILITY,
                PDATE, NOTICEYN, CDATE
            ) VALUES (
                {SEQ_CCTV_ISSUE}.NEXTVAL, :cno, NULL, :code, 0, :comnet, :reliability,
                NULL, 'N', :cdate
            )
            """,
            cno=cno,
            code=code,
            comnet=comnet,
            reliability=reliability_str,
            cdate=cdate,
        )

        connection.commit()

        return {
            "cno": cno,
            "code": code,
            "state": 0,
            "comnet": comnet,
            "reliability": reliability_str,
            "noticeyn": "N",
            "cdate": cdate,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()
