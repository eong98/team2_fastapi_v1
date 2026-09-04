from datetime import datetime

from core.database import get_connection
from shopmap.service import create_issue_map
from delivery.email import send_notification_email

# ========================================
# Sequence
# ========================================

SEQ_NOTIFICATION = "SEQ_NOTIFICATION_NO"
SEQ_SENDLOG = "SEQ_SENDLOG_NO"


# ========================================
# 알림 처리 상태
# ========================================

STATUS_READY = "READY"
STATUS_SENDING = "SENDING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ========================================
# 1. CCTV 이슈 전체 처리
# ========================================


def process_cctv_issue(
    cino: int,
    state: int,
    sno: int,
    cno: int,
    xpos: float | None = None,
    ypos: float | None = None,
) -> dict:
    """
    CCTV 이슈 1건을 기준으로
    이슈맵 생성 → 회원 조회 → 회원별 알림 저장 →
    알림 처리 결과 저장까지 전체 흐름을 처리한다.

    X/Y 좌표는 추후 CCTV(Jetson) 연동 예정.
    """

    try:

        # ====================================
        # 1. CCTV 최신 STATE 반영
        # ====================================

        update_cctv_state(
            cino=cino,
            state=state,
        )

        # ====================================
        # 2. 이슈맵 생성 / 조회
        # ====================================

        issue_map = get_issue_map(
            cino=cino,
            sno=sno,
            cno=cno,
            xpos=xpos,
            ypos=ypos,
        )

        aimapno = issue_map.get("aimapno")

        # ====================================
        # 3. 해당 매장 회원 조회
        # ====================================

        members = find_members(sno=sno)

        if not members:
            raise ValueError("알림을 받을 매장 회원이 없습니다.")

        success_count = 0
        fail_count = 0

        # ====================================
        # 회원별 알림 처리
        # ====================================

        for member in members:

            try:

                # ----------------------------
                # 회원별 NOTIFICATION 저장
                # 최초 STATUS = READY
                # ----------------------------

                notification = create_notification(
                    cino=cino,
                    mno=member["mno"],
                    aimapno=aimapno,
                    content=issue_map["issue"],
                )

                # ----------------------------
                # 일반 알림 처리
                # READY → SENDING → SENT/FAILED
                # ----------------------------

                send_member_notification(
                    notification=notification,
                    member=member,
                )

                success_count += 1

            except Exception as e:

                fail_count += 1

                print(f"[notification] " f"MNO={member['mno']} 알림 처리 실패: {e}")

        # ====================================
        # 4. CCTV 알림 처리 완료
        # ====================================

        # 회원 전체 처리 성공 후
        # CCTV 이슈의 알림 처리 여부 변경
        if fail_count == 0:
            update_cctv_notice(
                cino=cino,
                noticeyn="Y",
            )

        return {
            "cino": cino,
            "processedMembers": len(members),
            "successCount": success_count,
            "failCount": fail_count,
            "message": "CCTV 이슈 알림 처리가 완료되었습니다.",
        }

    except Exception:
        # CCTV 이슈 전체 처리 중
        # 예외가 발생하면 상위 Router로 전달
        raise


# ========================================
# 2. 이슈맵 받는 함수
# ========================================


def get_issue_map(
    cino: int,
    sno: int,
    cno: int,
    xpos: float | None = None,
    ypos: float | None = None,
) -> dict:
    """
    CCTV 이슈 내용을 조회하고
    매장 도면을 찾아 AI 이슈맵을 생성한다.

    X/Y가 없는 현재 개발 단계에서도
    일반 알림 처리는 계속 진행할 수 있도록 한다.
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        # ------------------------------------
        # CCTV 이슈 내용 조회
        # ------------------------------------

        cursor.execute(
            """
            SELECT
                CODE,
                COMNET
            FROM CCTV_ISSUE
            WHERE NO = :cino
              AND CNO = :cno
            """,
            {
                "cino": cino,
                "cno": cno,
            },
        )

        issue_row = cursor.fetchone()

        if issue_row is None:
            raise ValueError("CCTV 이슈 정보를 찾을 수 없습니다.")

        code = issue_row[0]
        issue = issue_row[1]

        # ------------------------------------
        # 매장 도면 번호 조회
        # ------------------------------------

        cursor.execute(
            """
            SELECT NO
            FROM SHOPMAP
            WHERE SNO = :sno
            ORDER BY NO DESC
            FETCH FIRST 1 ROWS ONLY
            """,
            {"sno": sno},
        )

        shopmap_row = cursor.fetchone()

        # ------------------------------------
        # 도면이 없는 경우
        # 일반 알림은 계속 처리
        # ------------------------------------

        if shopmap_row is None:

            return {
                "success": False,
                "aimapno": None,
                "code": code,
                "issue": issue,
                "message": "등록된 매장 도면이 없습니다.",
            }

        shopmapno = shopmap_row[0]

    finally:
        cursor.close()
        conn.close()

    # ------------------------------------
    # 현재 X/Y가 없는 경우
    # 일반 알림만 처리
    # ------------------------------------

    if xpos is None or ypos is None:

        return {
            "success": False,
            "aimapno": None,
            "code": code,
            "issue": issue,
            "message": "X/Y 좌표 미연동",
        }

    # ------------------------------------
    # X/Y가 있는 경우 AI 이슈맵 생성
    # ------------------------------------

    result = create_issue_map(
        shopmapno=shopmapno,
        cino=cino,
        issue=issue,
        xpos=xpos,
        ypos=ypos,
    )

    return {
        "success": result.get("success", False),
        "aimapno": result.get("aimapno"),
        "code": code,
        "issue": issue,
        "message": result.get("message"),
    }


# ========================================
# 3. 매장 회원 정보 찾는 함수
# ========================================


def find_members(sno: int) -> list[dict]:
    """
    해당 매장의 점주 + 소속 직원을 조회한다.

    점주:
    - SHOP.MNO

    직원:
    - SHOP_MEMBER.SNO 기준으로 회원번호 조회

    현재 필요한 정보:
    - 회원번호 : NOTIFICATION 저장
    - 이메일   : Java 이메일 발송
    - 전화번호 : Python 문자 발송
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """         
            SELECT
                M.NO,
                M.EMAIL,
                M.PHONE
            FROM MEMBER M
            WHERE M.NO = (
                SELECT S.MNO
                FROM SHOP S
                WHERE S.NO = :sno
            )

            UNION
           
            SELECT
                M.NO,
                M.EMAIL,
                M.PHONE
            FROM SHOP_MEMBER SM
            JOIN MEMBER M
              ON SM.MNO = M.NO
            WHERE SM.SNO = :sno
            """,
            {"sno": sno},
        )

        rows = cursor.fetchall()

        return [
            {
                "mno": row[0],
                "email": row[1],
                "phone": row[2],
            }
            for row in rows
        ]

    finally:
        cursor.close()
        conn.close()


# ========================================
# 회원별 NOTIFICATION 저장
# ========================================


def create_notification(
    cino: int,
    mno: int,
    aimapno: int | None,
    content: str,
) -> dict:
    """
    회원 한 명당 NOTIFICATION 1건 저장.

    최초 처리 상태:
    READY
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        # ------------------------------------
        # 알림번호 생성
        # ------------------------------------

        cursor.execute(f"""
            SELECT {SEQ_NOTIFICATION}.NEXTVAL
            FROM DUAL
            """)

        nno = cursor.fetchone()[0]

        # ------------------------------------
        # 회원별 알림 저장
        # ------------------------------------

        cursor.execute(
            """
            INSERT INTO NOTIFICATION (
                NO,
                CINO,
                MNO,
                ATITLE,
                CONTENT,
                STATUS,
                READYN,
                ASMNO,
                CDATE
            )
            VALUES (
                :no,
                :cino,
                :mno,
                :atitle,
                :content,
                :status,
                'N',
                :aimapno,
                :cdate
            )
            """,
            {
                "no": nno,
                "cino": cino,
                "mno": mno,
                "atitle": "CCTV 이슈 알림",
                "content": content,
                "status": STATUS_READY,
                "aimapno": aimapno,
                "cdate": _now(),
            },
        )

        conn.commit()

        return {
            "no": nno,
            "cino": cino,
            "mno": mno,
            "title": "CCTV 이슈 알림",
            "aimapno": aimapno,
            "content": content,
            "status": STATUS_READY,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ========================================
# 회원별 알림 처리
# ========================================


def send_member_notification(
    notification: dict,
    member: dict,
):
    """
    회원 한 명의 일반 알림 처리.

    현재:
    READY → SENDING → SENT

    추후:
    EMAIL → Java
    SMS   → Python
    SENDLOG → EMAIL / SMS 발송 결과 저장
    """

    nno = notification["no"]

    try:

        # ------------------------------------
        # READY → SENDING
        # ------------------------------------

        update_notification_status(
            nno=nno,
            status=STATUS_SENDING,
        )

        # ====================================
        # 이메일 발송
        # 추후 Java 연결
        # ====================================

        send_notification_email(
            notification_no=nno,
        )

        # ====================================
        # 문자 발송
        # 추후 Python tool/sms_service.py 연결
        # ====================================

        # send_sms(...)

        # ------------------------------------
        # SENDING → SENT
        # ------------------------------------

        update_notification_status(
            nno=nno,
            status=STATUS_SENT,
        )

    except Exception:

        # ------------------------------------
        # 실패 시 FAILED
        # ------------------------------------

        update_notification_status(
            nno=nno,
            status=STATUS_FAILED,
        )

        raise


# ========================================
# 4. 알림 처리 상태 변경
# ========================================


def update_notification_status(
    nno: int,
    status: str,
):
    """
    NOTIFICATION.STATUS 변경.

    READY
    SENDING
    SENT
    FAILED
    CANCELLED
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            UPDATE NOTIFICATION
            SET STATUS = :status
            WHERE NO = :nno
            """,
            {
                "status": status,
                "nno": nno,
            },
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ========================================
# 4. SENDLOG 저장
# ========================================


def save_send_log(
    nno: int,
    channel: str,
    status: int,
    message: str,
):
    """
    알림 처리 결과 기록.

    현재:
        NOTIFICATION

    추후:
        EMAIL
        SMS

    성공/실패와 이유를 MESSAGE에 저장한다.
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            f"""
            INSERT INTO SENDLOG (
                NO,
                NNO,
                CHANNEL,
                STATUS,
                MESSAGE,
                CDATE
            )
            VALUES (
                {SEQ_SENDLOG}.NEXTVAL,
                :nno,
                :channel,
                :status,
                :message,
                :cdate
            )
            """,
            {
                "nno": nno,
                "channel": channel,
                "status": status,
                "message": message,
                "cdate": _now(),
            },
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ========================================
# CCTV STATE 반영
# ========================================


def update_cctv_state(
    cino: int,
    state: int,
):
    """
    CCTV에서 전달된 최신 STATE 반영.
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            UPDATE CCTV_ISSUE
            SET STATE = :state
            WHERE NO = :cino
            """,
            {
                "state": state,
                "cino": cino,
            },
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ========================================
# CCTV 알림 처리 여부
# ========================================


def update_cctv_notice(
    cino: int,
    noticeyn: str,
):
    """
    CCTV_ISSUE 알림 처리 여부.

    N = 미처리
    Y = 처리 완료
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            UPDATE CCTV_ISSUE
            SET NOTICEYN = :noticeyn
            WHERE NO = :cino
            """,
            {
                "noticeyn": noticeyn,
                "cino": cino,
            },
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()
