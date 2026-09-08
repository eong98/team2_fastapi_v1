import base64
import uuid
import requests

from .sms_token import (
    get_access_token,
    GabiaSSLAdapter,
    SMS_ID,
)

# ========================================
# 가비아 문자 발송 API
# ========================================

SMS_SEND_URL = "https://sms.gabia.com/api/send/sms"
LMS_SEND_URL = "https://sms.gabia.com/api/send/lms"

# 가비아 관리툴에 등록한 실제 발신번호
# 하이픈 없이 작성
CALLBACK_NUMBER = "01027229751"


# ========================================
# SMS / LMS 문자 발송
# ========================================


def send_notification_sms(phone: str, message: str) -> bool:
    """
    가비아 문자 API를 이용해 문자를 발송한다.

    1. sms_token.py에서 Access Token 발급
    2. SMS_ID + ACCESS_TOKEN Base64 인코딩
    3. 메시지 길이 확인
       - 90byte 이하  → SMS
       - 90byte 초과  → LMS
    4. 가비아 문자 발송 API 호출
    5. 성공/실패 반환
    """

    try:

        # ----------------------------------------
        # Access Token 발급
        # ----------------------------------------

        access_token = get_access_token()

        # ----------------------------------------
        # SMS_ID:ACCESS_TOKEN Base64 인코딩
        # ----------------------------------------

        auth_string = f"{SMS_ID}:{access_token}"

        auth_value = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

        # ----------------------------------------
        # Header
        # ----------------------------------------

        headers = {
            "Authorization": f"Basic {auth_value}",
            "Content-Type": "application/x-www-form-urlencoded",
            "cache-control": "no-cache",
        }

        # ----------------------------------------
        # SMS / LMS 구분
        # ----------------------------------------

        message_bytes = len(message.encode("utf-8"))

        if message_bytes > 90:
            send_url = LMS_SEND_URL
            message_type = "LMS"
        else:
            send_url = SMS_SEND_URL
            message_type = "SMS"

        # ----------------------------------------
        # 발송 데이터
        # ----------------------------------------

        refkey = uuid.uuid4().hex

        data = {
            "phone": phone,
            "callback": CALLBACK_NUMBER,
            "message": message,
            "refkey": refkey,
        }

        # ----------------------------------------
        # 가비아 SSL 호환 Session
        # ----------------------------------------

        session = requests.Session()

        session.mount(
            "https://sms.gabia.com",
            GabiaSSLAdapter(),
        )

        # ----------------------------------------
        # 문자 발송
        # ----------------------------------------

        response = session.post(
            send_url,
            headers=headers,
            data=data,
            timeout=10,
            allow_redirects=False,
        )

        # ----------------------------------------
        # 결과 처리
        # ----------------------------------------

        if response.status_code == 200:

            print(
                f"-> {message_type} 발송 성공 "
                f"(phone={phone}, "
                f"bytes={message_bytes}, "
                f"refkey={refkey})"
            )

            return True

        print(
            f"-> {message_type} 발송 실패 "
            f"(phone={phone}, "
            f"bytes={message_bytes}, "
            f"status={response.status_code}, "
            f"response={response.text})"
        )

        return False

    except requests.RequestException as e:

        print(f"-> 문자 발송 API 호출 오류 " f"(phone={phone}): {e}")

        return False

    except Exception as e:

        print(f"-> 문자 발송 오류 " f"(phone={phone}): {e}")

        return False
