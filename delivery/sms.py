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
# 수신번호 마스킹
# ========================================


def _mask_phone(phone: str) -> str:
    """
    콘솔에 전화번호 전체가 노출되지 않도록 마스킹한다.
    """

    if not phone:
        return "없음"

    phone = str(phone)

    if len(phone) >= 8:
        return f"{phone[:3]}****{phone[-4:]}"

    return "****"


# ========================================
# 가비아 문자 발송 오류 분석
# ========================================


def _print_send_error(
    status_code: int,
    response_text: str,
    phone: str,
    message_type: str,
    message_bytes: int,
    refkey: str,
):
    """
    가비아 문자 발송 실패 원인을
    콘솔에서 확인하기 쉽게 출력한다.
    """

    print("\n========================================")
    print(f"[SMS][SEND][FAIL] {message_type} 발송 실패")
    print("========================================")
    print(f"- 수신번호: {_mask_phone(phone)}")
    print(f"- HTTP 상태: {status_code}")
    print(f"- 메시지 크기: {message_bytes} bytes")
    print(f"- refkey: {refkey}")

    response_lower = response_text.lower()

    if "api 발송 ip" in response_lower:
        print("- 원인: API 발송 허용 IP가 등록되지 않았습니다.")
        print("- 확인: 가비아 관리툴의 API 발송 IP 설정")

    elif "callback" in response_lower:
        print("- 원인: 발신번호 등록 또는 인증 문제 가능성이 있습니다.")
        print(f"- 확인 발신번호: {CALLBACK_NUMBER}")

    elif "phone" in response_lower:
        print("- 원인: 수신번호 형식 또는 수신번호 관련 오류 가능성이 있습니다.")

    elif status_code == 400:
        print("- 원인: 문자 발송 요청 데이터가 올바르지 않을 가능성이 있습니다.")
        print("- 확인: 수신번호 / 발신번호 / 메시지 내용")

    elif status_code == 401:
        print("- 원인: Access Token 또는 SMS 인증정보 문제 가능성이 있습니다.")
        print("- 확인: SMS_ID / API_KEY / Access Token")

    elif status_code == 403:
        print("- 원인: 문자 발송 API 접근 권한이 없습니다.")
        print("- 확인: API 사용 권한 / 발송 IP / 발신번호")

    elif status_code == 404:
        print("- 원인: 문자 발송 API 주소를 찾을 수 없습니다.")

    elif status_code == 429:
        print("- 원인: 문자 API 요청 횟수 제한 가능성이 있습니다.")

    elif status_code >= 500:
        print("- 원인: 가비아 문자 서버 오류 가능성이 있습니다.")

    else:
        print("- 원인: 문자 발송 API 요청이 실패했습니다.")

    print(f"- 가비아 응답: {response_text}")
    print("========================================\n")


# ========================================
# SMS / LMS 문자 발송
# ========================================


def send_notification_sms(phone: str, message: str) -> bool:
    """
    가비아 문자 API를 이용해 문자를 발송한다.

    1. 수신번호 / 메시지 기본값 검사
    2. sms_token.py에서 Access Token 발급
    3. SMS_ID + ACCESS_TOKEN Base64 인코딩
    4. 메시지 길이 확인
       - 90byte 이하 → SMS
       - 90byte 초과 → LMS
    5. 가비아 문자 발송 API 호출
    6. 성공/실패 반환
    """

    # ----------------------------------------
    # 기본 데이터 검사
    # ----------------------------------------

    if not phone:
        print("\n[SMS][VALIDATION][FAIL] 문자 발송 대상 전화번호가 없습니다.")
        return False

    phone = str(phone).strip().replace("-", "")

    if not phone.isdigit():
        print("\n[SMS][VALIDATION][FAIL] 전화번호 형식이 올바르지 않습니다.")
        print(f"- 수신번호: {_mask_phone(phone)}")
        print("- 원인: 숫자가 아닌 문자가 포함되어 있습니다.")
        return False

    if not message or not str(message).strip():
        print("\n[SMS][VALIDATION][FAIL] 발송할 문자 내용이 없습니다.")
        print(f"- 수신번호: {_mask_phone(phone)}")
        return False

    message = str(message)

    print(f"[SMS][START] 문자 발송 시작 " f"(phone={_mask_phone(phone)})")

    try:
        # ----------------------------------------
        # Access Token 발급
        # ----------------------------------------

        try:
            access_token = get_access_token()

        except Exception as e:
            print("\n[SMS][TOKEN][STOP] 토큰 발급 실패로 문자 발송을 중단합니다.")
            print(f"- 수신번호: {_mask_phone(phone)}")
            print(f"- 상세 오류: {e}")
            return False

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

        print(f"[SMS][TYPE] {message_type} 발송 " f"(bytes={message_bytes})")

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

        try:
            response = session.post(
                send_url,
                headers=headers,
                data=data,
                timeout=10,
                allow_redirects=False,
            )

        except requests.exceptions.ConnectTimeout:
            print("\n[SMS][SEND][TIMEOUT] 가비아 문자 서버 연결 시간 초과")
            print(f"- 수신번호: {_mask_phone(phone)}")
            print(f"- 요청 URL: {send_url}")
            print("- 확인: H200 인터넷 연결 / 방화벽 / 가비아 서버 상태")
            return False

        except requests.exceptions.ConnectionError as e:
            print("\n[SMS][SEND][CONNECTION] 가비아 문자 서버 연결 실패")
            print(f"- 수신번호: {_mask_phone(phone)}")
            print(f"- 요청 URL: {send_url}")
            print(f"- 상세 오류: {e}")
            print("- 확인: 네트워크 / DNS / 방화벽")
            return False

        except requests.exceptions.SSLError as e:
            print("\n[SMS][SEND][SSL] 가비아 문자 서버 SSL 오류")
            print(f"- 상세 오류: {e}")
            return False

        except requests.RequestException as e:
            print("\n[SMS][SEND][REQUEST] 문자 발송 API 호출 오류")
            print(f"- 수신번호: {_mask_phone(phone)}")
            print(f"- 상세 오류: {e}")
            return False

        # ----------------------------------------
        # 결과 처리
        # ----------------------------------------

        if response.status_code == 200:
            print(
                f"[SMS][SEND][SUCCESS] {message_type} 발송 성공 "
                f"(phone={_mask_phone(phone)}, "
                f"bytes={message_bytes}, "
                f"refkey={refkey})"
            )

            return True

        _print_send_error(
            status_code=response.status_code,
            response_text=response.text,
            phone=phone,
            message_type=message_type,
            message_bytes=message_bytes,
            refkey=refkey,
        )

        return False

    except Exception as e:
        print("\n[SMS][UNKNOWN][FAIL] 문자 발송 중 예상하지 못한 오류 발생")
        print(f"- 수신번호: {_mask_phone(phone)}")
        print(f"- 오류 타입: {type(e).__name__}")
        print(f"- 상세 오류: {e}")

        return False
