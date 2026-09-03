import os
import requests


# ========================================
# Java 이메일 발송 API
# ========================================

JAVA_BASE_URL = os.getenv("JAVA_BASE_URL")


# ========================================
# 알림 이메일 발송 요청
# ========================================

def send_notification_email(notification_no: int) -> bool:
    """
    저장된 NOTIFICATION 번호를 Java 서버에 전달하여
    해당 회원에게 이메일 발송을 요청한다.

    Java 서버에서:
    1. NOTIFICATION 조회
    2. MNO로 MEMBER 조회
    3. MEMBER.EMAIL 조회
    4. MailService로 이메일 발송
    5. SENDLOG에 성공/실패 기록
    """

    url = f"{JAVA_BASE_URL}/api/notifications/{notification_no}/email"

    try:
        response = requests.post(
            url,
            timeout=10
        )

        if response.status_code == 200:
            print(
                f"-> 이메일 발송 성공 "
                f"(notification_no={notification_no})"
            )
            return True

        print(
            f"-> 이메일 발송 실패 "
            f"(notification_no={notification_no}, "
            f"status={response.status_code}, "
            f"response={response.text})"
        )
        return False

    except requests.RequestException as e:
        print(
            f"-> 이메일 발송 API 호출 오류 "
            f"(notification_no={notification_no}): {e}"
        )
        return False