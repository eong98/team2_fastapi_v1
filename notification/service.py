from datetime import datetime

from core.database import get_connection
from delivery.email import send_notification_email
from delivery.sms import send_notification_sms

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
    sno: int,
    cno: int,
    asmno: int | None = None,
) -> dict:
    """
    CCTV 이슈 1건을 기준으로 회원을 조회하고
    회원별 NOTIFICATION 저장 및 문자/메일 발송을 처리한다.

    asmno:
    - AI 이슈 도면 생성 성공 → AIISSUEMAP.NO
    - 도면 없음/생성 안 됨 → None
    """

    # CCTV 이슈 내용 조회
    issue = find_cctv_issue(
        cino=cino,
        cno=cno,
    )

    # AI 이슈맵이 생성된 경우 파일명 조회
    fsaved = find_ai_issue_map_file(asmno)

    # 해당 매장의 점주 + 직원 조회
    members = find_members(sno=sno)

    if not members:
        raise ValueError("알림을 받을 매장 회원이 없습니다.")

    success_count = 0
    fail_count = 0

    # 회원별 알림 처리
    for member in members:
        try:
            notification = create_notification(
                cino=cino,
                mno=member["mno"],
                asmno=asmno,
                content=issue["content"],
            )

            send_member_notification(
                notification=notification,
                member=member,
                fsaved=fsaved,
            )

            success_count += 1

        except Exception as e:
            fail_count += 1

            print(f"[notification] " f"MNO={member['mno']} 알림 처리 실패: {e}")

    # 모든 회원의 알림 처리가 성공한 경우
    if fail_count == 0:
        update_cctv_notice(
            cino=cino,
            noticeyn="Y",
        )

    return {
        "cino": cino,
        "asmno": asmno,
        "processedMembers": len(members),
        "successCount": success_count,
        "failCount": fail_count,
        "message": "CCTV 이슈 알림 처리가 완료되었습니다.",
    }


# ========================================
# 2. CCTV 이슈 내용 조회
# ========================================


def find_cctv_issue(
    cino: int,
    cno: int,
) -> dict:
    """CCTV_ISSUE에서 알림에 사용할 이슈 정보를 조회한다."""

    conn = get_connection()
    cursor = conn.cursor()

    try:
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

        row = cursor.fetchone()

        if row is None:
            raise ValueError("CCTV 이슈 정보를 찾을 수 없습니다.")

        code = row[0]
        content = row[1]

        # Oracle CLOB 대응
        if hasattr(content, "read"):
            content = content.read()

        return {
            "code": code,
            "content": content or "",
        }

    finally:
        cursor.close()
        conn.close()


# ========================================
# 3. AI 이슈맵 파일 조회
# ========================================


def find_ai_issue_map_file(
    asmno: int | None,
) -> str | None:
    """
    AIISSUEMAP.NO로 생성된 이미지 파일명을 조회한다.

    asmno가 None이면 도면이 없는 알림이므로
    파일 조회 없이 None을 반환한다.
    """

    if asmno is None:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT FSAVED
            FROM AIISSUEMAP
            WHERE NO = :asmno
              AND STATUS = 1
            """,
            {
                "asmno": asmno,
            },
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return row[0]

    finally:
        cursor.close()
        conn.close()


# ========================================
# 4. 매장 회원 조회
# ========================================


def find_members(sno: int) -> list[dict]:
    """
    해당 매장의 점주 + 소속 직원을 조회한다.

    점주:
    SHOP.MNO

    직원:
    SHOP_MEMBER.SNO 기준 회원 조회
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
            {
                "sno": sno,
            },
        )

        rows = cursor.fetchall()

        return [
            {
                "mno": int(row[0]),
                "email": row[1],
                "phone": row[2],
            }
            for row in rows
        ]

    finally:
        cursor.close()
        conn.close()


# ========================================
# 5. 회원별 NOTIFICATION 저장
# ========================================


def create_notification(
    cino: int,
    mno: int,
    asmno: int | None,
    content: str,
) -> dict:
    """
    회원 한 명당 NOTIFICATION 1건 저장.

    ASMNO:
    AI 이슈 도면 있음 → AIISSUEMAP.NO
    AI 이슈 도면 없음 → NULL
    """

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 알림번호 생성
        cursor.execute(f"""
            SELECT {SEQ_NOTIFICATION}.NEXTVAL
            FROM DUAL
            """)

        nno = int(cursor.fetchone()[0])

        # 회원별 알림 저장
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
                :asmno,
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
                "asmno": asmno,
                "cdate": _now(),
            },
        )

        conn.commit()

        return {
            "no": nno,
            "cino": cino,
            "mno": mno,
            "title": "CCTV 이슈 알림",
            "content": content,
            "asmno": asmno,
            "status": STATUS_READY,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ========================================
# 6. 회원별 문자 / 이메일 발송
# ========================================


def send_member_notification(
    notification: dict,
    member: dict,
    fsaved: str | None = None,
):
    """
    회원 한 명의 이메일/문자 알림을 발송한다.

    도면 있음:
    이메일 + 문자에 AI 이슈맵 연결

    도면 없음:
    이슈 내용만 발송
    """

    nno = notification["no"]

    try:
        # READY → SENDING
        update_notification_status(
            nno=nno,
            status=STATUS_SENDING,
        )

        # ====================================
        # 이메일 발송
        # ====================================

        # Java 이메일 서비스에서 NOTIFICATION.NO를 조회하여
        # CINO / ASMNO 등의 알림 정보를 사용한다.
        send_notification_email(
            notification_no=nno,
        )

        # ====================================
        # 문자 발송
        # ====================================

        phone = member.get("phone")

        if phone:
            phone = phone.replace("-", "").strip()

            sms_message = notification["content"]

            # AI 이슈 도면이 존재하는 경우 이미지 조회 주소 추가
            if fsaved:
                image_url = (
                    "http://10.1.205.118:11200" "/api/aiissuemap/image/" + fsaved
                )

                sms_message += f"\n이슈 위치: {image_url}"

            send_notification_sms(
                phone=phone,
                message=sms_message,
            )

        # SENDING → SENT
        update_notification_status(
            nno=nno,
            status=STATUS_SENT,
        )

    except Exception:
        # 발송 실패
        update_notification_status(
            nno=nno,
            status=STATUS_FAILED,
        )

        raise


# ========================================
# 7. NOTIFICATION 상태 변경
# ========================================


def update_notification_status(
    nno: int,
    status: str,
):
    """NOTIFICATION.STATUS를 변경한다."""

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
# 8. SENDLOG 저장
# ========================================


def save_send_log(
    nno: int,
    channel: str,
    status: int,
    message: str,
):
    """EMAIL / SMS 발송 결과를 SENDLOG에 저장한다."""

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
# 9. CCTV 알림 처리 여부
# ========================================


def update_cctv_notice(
    cino: int,
    noticeyn: str,
):
    """
    CCTV_ISSUE.NOTICEYN 변경.

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
