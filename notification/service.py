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
    """

    print("\n========================================")
    print("[NOTIFICATION][START] CCTV 이슈 알림 처리 시작")
    print("========================================")
    print(f"- CCTV 이슈번호: {cino}")
    print(f"- 매장번호: {sno}")
    print(f"- CCTV번호: {cno}")
    print(f"- AI 이슈맵번호: {asmno}")

    # ----------------------------------------
    # CCTV 이슈 조회
    # ----------------------------------------

    try:
        issue = find_cctv_issue(
            cino=cino,
            cno=cno,
        )

        print(
            f"[NOTIFICATION][ISSUE][SUCCESS] "
            f"CCTV 이슈 조회 성공 (code={issue['code']})"
        )

    except Exception as e:
        print("\n[NOTIFICATION][ISSUE][FAIL] CCTV 이슈 조회 실패")
        print(f"- CINO: {cino}")
        print(f"- CNO: {cno}")
        print(f"- 상세 오류: {e}")
        raise

    # ----------------------------------------
    # AI 이슈맵 조회
    # ----------------------------------------

    fsaved = find_ai_issue_map_file(asmno)

    if asmno is None:
        print(
            "[NOTIFICATION][AIMAP][SKIP] "
            "AI 이슈맵 번호가 없습니다. 이슈 내용만 발송합니다."
        )

    elif fsaved:
        print(
            f"[NOTIFICATION][AIMAP][SUCCESS] "
            f"AI 이슈맵 조회 성공 (asmno={asmno}, fsaved={fsaved})"
        )

    else:
        print(
            f"[NOTIFICATION][AIMAP][SKIP] "
            f"사용 가능한 AI 이슈맵이 없습니다. "
            f"(asmno={asmno})"
        )

    # ----------------------------------------
    # 매장 회원 조회
    # ----------------------------------------

    try:
        members = find_members(sno=sno)

    except Exception as e:
        print("\n[NOTIFICATION][MEMBER][FAIL] 매장 회원 DB 조회 실패")
        print(f"- 매장번호: {sno}")
        print(f"- 상세 오류: {e}")
        raise

    if not members:
        print("\n[NOTIFICATION][MEMBER][FAIL] 알림 대상 회원이 없습니다.")
        print(f"- 매장번호: {sno}")
        print("- 확인: SHOP.MNO / SHOP_MEMBER.SNO / MEMBER")
        raise ValueError("알림을 받을 매장 회원이 없습니다.")

    print(f"[NOTIFICATION][MEMBER][SUCCESS] " f"알림 대상 {len(members)}명 조회")

    success_count = 0
    fail_count = 0

    # ----------------------------------------
    # 회원별 알림 처리
    # ----------------------------------------

    for member in members:

        mno = member["mno"]

        print("\n----------------------------------------")
        print(f"[NOTIFICATION][MEMBER][START] MNO={mno}")
        print("----------------------------------------")

        try:
            notification = create_notification(
                cino=cino,
                mno=mno,
                asmno=asmno,
                content=issue["content"],
            )

            print(
                f"[NOTIFICATION][DB][SUCCESS] "
                f"NOTIFICATION 저장 완료 "
                f"(nno={notification['no']}, mno={mno})"
            )

            send_member_notification(
                notification=notification,
                member=member,
                fsaved=fsaved,
            )

            success_count += 1

            print(f"[NOTIFICATION][MEMBER][SUCCESS] " f"MNO={mno} 알림 처리 완료")

        except Exception as e:
            fail_count += 1

            print("\n[NOTIFICATION][MEMBER][FAIL] 회원 알림 처리 실패")
            print(f"- 회원번호: {mno}")
            print(f"- 상세 오류: {e}")

    # ----------------------------------------
    # CCTV 알림 처리 여부
    # ----------------------------------------

    if fail_count == 0:
        update_cctv_notice(
            cino=cino,
            noticeyn="Y",
        )

        print(f"[NOTIFICATION][CCTV][SUCCESS] " f"CCTV_ISSUE.NOTICEYN=Y (cino={cino})")

    else:
        print(
            f"[NOTIFICATION][CCTV][SKIP] "
            f"발송 실패 {fail_count}건이 있어 "
            f"NOTICEYN을 Y로 변경하지 않습니다."
        )

    print("\n========================================")
    print("[NOTIFICATION][END] CCTV 이슈 알림 처리 종료")
    print(f"- 대상 회원: {len(members)}명")
    print(f"- 성공: {success_count}명")
    print(f"- 실패: {fail_count}명")
    print("========================================\n")

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
            raise ValueError(
                f"CCTV 이슈 정보를 찾을 수 없습니다. " f"(cino={cino}, cno={cno})"
            )

        code = row[0]
        content = row[1]

        # Oracle CLOB 대응
        if hasattr(content, "read"):
            content = content.read()

        return {
            "code": code,
            "content": content or "",
        }

    except Exception as e:
        print("[NOTIFICATION][DB][FAIL] CCTV_ISSUE 조회 오류")
        print(f"- CINO: {cino}")
        print(f"- CNO: {cno}")
        print(f"- 상세 오류: {e}")
        raise

    finally:
        cursor.close()
        conn.close()


# ========================================
# 3. AI 이슈맵 파일 조회
# ========================================


def find_ai_issue_map_file(
    asmno: int | None,
) -> str | None:
    """AIISSUEMAP.NO로 생성된 이미지 파일명을 조회한다."""

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
            print(
                f"[NOTIFICATION][AIMAP][NOT_FOUND] "
                f"정상 생성된 AI 이슈맵을 찾을 수 없습니다. "
                f"(asmno={asmno})"
            )
            return None

        return row[0]

    except Exception as e:
        print("[NOTIFICATION][AIMAP][DB_FAIL] AIISSUEMAP 조회 실패")
        print(f"- ASMNO: {asmno}")
        print(f"- 상세 오류: {e}")
        return None

    finally:
        cursor.close()
        conn.close()


# ========================================
# 4. 매장 회원 조회
# ========================================


def find_members(sno: int) -> list[dict]:
    """해당 매장의 점주 + 소속 직원을 조회한다."""

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

    except Exception as e:
        print("[NOTIFICATION][MEMBER][DB_FAIL] 회원 조회 SQL 실패")
        print(f"- 매장번호: {sno}")
        print(f"- 상세 오류: {e}")
        raise

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
    """회원 한 명당 NOTIFICATION 1건 저장."""

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(f"""
            SELECT {SEQ_NOTIFICATION}.NEXTVAL
            FROM DUAL
            """)

        nno = int(cursor.fetchone()[0])

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

    except Exception as e:
        conn.rollback()

        print("\n[NOTIFICATION][DB][FAIL] NOTIFICATION 저장 실패")
        print(f"- CINO: {cino}")
        print(f"- MNO: {mno}")
        print(f"- ASMNO: {asmno}")
        print(f"- 상세 오류: {e}")

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
    """회원 한 명의 이메일/문자 알림을 발송한다."""

    nno = notification["no"]
    mno = member["mno"]

    email = member.get("email")
    phone = member.get("phone")

    try:
        # READY → SENDING
        update_notification_status(
            nno=nno,
            status=STATUS_SENDING,
        )

        print(f"[NOTIFICATION][STATUS] " f"NNO={nno} READY → SENDING")

        # ====================================
        # 이메일 발송
        # ====================================

        email_success = False

        if email and str(email).strip():

            print(f"[NOTIFICATION][EMAIL][START] " f"MNO={mno}, NNO={nno}")

            email_success = send_notification_email(
                notification_no=nno,
            )

            if email_success:
                print(f"[NOTIFICATION][EMAIL][SUCCESS] " f"MNO={mno}")
            else:
                print(f"[NOTIFICATION][EMAIL][FAIL] " f"MNO={mno}")

        else:
            print(
                f"[NOTIFICATION][EMAIL][SKIP] "
                f"MNO={mno} DB에 이메일 주소가 없습니다."
            )

        # ====================================
        # 문자 발송
        # ====================================

        sms_success = False

        if phone and str(phone).strip():

            phone = str(phone).replace("-", "").strip()

            sms_message = notification["content"]

            # AI 이슈맵 존재 시 이미지 조회 주소 추가
            if fsaved:
                image_url = (
                    "http://10.1.205.118:11200" "/api/aiissuemap/image/" + fsaved
                )

                sms_message += f"\n이슈 위치: {image_url}"

            print(f"[NOTIFICATION][SMS][START] " f"MNO={mno}, NNO={nno}")

            sms_success = send_notification_sms(
                phone=phone,
                message=sms_message,
            )

            if sms_success:
                print(f"[NOTIFICATION][SMS][SUCCESS] " f"MNO={mno}")
            else:
                print(f"[NOTIFICATION][SMS][FAIL] " f"MNO={mno}")

        else:
            print(f"[NOTIFICATION][SMS][SKIP] " f"MNO={mno} DB에 전화번호가 없습니다.")

        # ====================================
        # 최종 발송 결과 판단
        # ====================================

        if email_success or sms_success:

            update_notification_status(
                nno=nno,
                status=STATUS_SENT,
            )

            print(f"[NOTIFICATION][STATUS] " f"NNO={nno} SENDING → SENT")

            return {
                "success": True,
                "email": email_success,
                "sms": sms_success,
            }

        # 이메일과 문자 모두 실패 또는 발송 불가
        update_notification_status(
            nno=nno,
            status=STATUS_FAILED,
        )

        print(f"[NOTIFICATION][STATUS] " f"NNO={nno} SENDING → FAILED")

        raise RuntimeError(
            f"이메일과 문자 발송이 모두 실패했습니다. " f"(mno={mno}, nno={nno})"
        )

    except Exception as e:

        try:
            update_notification_status(
                nno=nno,
                status=STATUS_FAILED,
            )
        except Exception as status_error:
            print(
                f"[NOTIFICATION][STATUS][FAIL] "
                f"FAILED 상태 변경 실패: {status_error}"
            )

        print("\n[NOTIFICATION][SEND][FAIL] 회원 알림 발송 실패")
        print(f"- 회원번호: {mno}")
        print(f"- 알림번호: {nno}")
        print(f"- 상세 오류: {e}")

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

        if cursor.rowcount == 0:
            raise ValueError(f"NOTIFICATION을 찾을 수 없습니다. (nno={nno})")

        conn.commit()

    except Exception as e:
        conn.rollback()

        print("[NOTIFICATION][STATUS][DB_FAIL] 상태 변경 실패")
        print(f"- NNO: {nno}")
        print(f"- 변경 상태: {status}")
        print(f"- 상세 오류: {e}")

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

    except Exception as e:
        conn.rollback()

        print("[NOTIFICATION][SENDLOG][FAIL] SENDLOG 저장 실패")
        print(f"- NNO: {nno}")
        print(f"- CHANNEL: {channel}")
        print(f"- 상세 오류: {e}")

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
    """CCTV_ISSUE.NOTICEYN 변경."""

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

        if cursor.rowcount == 0:
            raise ValueError(f"CCTV_ISSUE를 찾을 수 없습니다. (cino={cino})")

        conn.commit()

    except Exception as e:
        conn.rollback()

        print("[NOTIFICATION][CCTV][DB_FAIL] NOTICEYN 변경 실패")
        print(f"- CINO: {cino}")
        print(f"- NOTICEYN: {noticeyn}")
        print(f"- 상세 오류: {e}")

        raise

    finally:
        cursor.close()
        conn.close()
