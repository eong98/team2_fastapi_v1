import os

import requests

# ========================================
# Java 이메일 발송 API
# ========================================

JAVA_BASE_URL = os.getenv("JAVA_BASE_URL")


# ========================================
# Java API 오류 분석
# ========================================


def _print_email_error(
    notification_no: int,
    status_code: int,
    response_text: str,
):
    """
    Java 이메일 발송 API에서 반환한 오류를
    콘솔에서 확인하기 쉽게 출력한다.
    """

    print("\n========================================")
    print("[EMAIL][API][FAIL] 이메일 발송 실패")
    print("========================================")
    print(f"- 알림번호: {notification_no}")
    print(f"- HTTP 상태: {status_code}")

    response_lower = response_text.lower()

    # ----------------------------------------
    # HTTP 상태별 오류 분석
    # ----------------------------------------

    if status_code == 400:
        print("- 원인: Java 이메일 API 요청 데이터 오류 가능성이 있습니다.")
        print("- 확인: NOTIFICATION 번호 및 요청 형식")

    elif status_code == 401:
        print("- 원인: Java API 인증 실패 가능성이 있습니다.")
        print("- 확인: 인증 설정 및 접근 권한")

    elif status_code == 403:
        print("- 원인: Java 이메일 API 접근 권한이 없습니다.")
        print("- 확인: Spring Security / API 접근 권한")

    elif status_code == 404:
        print("- 원인: 알림 또는 이메일 발송 API를 찾을 수 없습니다.")
        print("- 확인: NOTIFICATION 번호 / Java API 주소")

    elif status_code == 409:
        print("- 원인: 이메일 발송 처리 중 데이터 충돌이 발생했습니다.")

    elif status_code >= 500:
        print("- 원인: Java 서버 내부에서 이메일 발송 처리 중 오류가 발생했습니다.")
        print("- 확인: Java 콘솔 / MailService / MEMBER / SENDLOG")

    else:
        print("- 원인: Java 이메일 API 요청이 실패했습니다.")

    # ----------------------------------------
    # 응답 내용으로 추가 원인 추정
    # ----------------------------------------

    if "email" in response_lower:
        print("- 추가 확인: 회원 이메일 정보 또는 이메일 형식을 확인하세요.")

    if "member" in response_lower:
        print("- 추가 확인: NOTIFICATION.MNO와 MEMBER 정보를 확인하세요.")

    if "notification" in response_lower:
        print("- 추가 확인: NOTIFICATION 데이터 존재 여부를 확인하세요.")

    if "mail" in response_lower:
        print("- 추가 확인: Java MailService 및 SMTP 설정을 확인하세요.")

    print(f"- Java 응답: {response_text}")
    print("========================================\n")


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

    # ----------------------------------------
    # 환경변수 확인
    # ----------------------------------------

    if not JAVA_BASE_URL:
        print("\n========================================")
        print("[EMAIL][CONFIG][FAIL] Java 서버 주소가 없습니다.")
        print("========================================")
        print("- 원인: JAVA_BASE_URL 환경변수가 설정되지 않았습니다.")
        print("- 확인: .env 파일의 JAVA_BASE_URL")
        print("- 예시: JAVA_BASE_URL=http://10.1.205.xxx:9102")
        print("========================================\n")

        return False

    # ----------------------------------------
    # 알림번호 확인
    # ----------------------------------------

    if not notification_no:
        print("\n[EMAIL][VALIDATION][FAIL] 알림번호가 없습니다.")
        return False

    url = f"{JAVA_BASE_URL.rstrip('/')}" f"/api/notifications/{notification_no}/email"

    print(f"[EMAIL][START] 이메일 발송 요청 " f"(notification_no={notification_no})")

    # ----------------------------------------
    # Java 이메일 API 호출
    # ----------------------------------------

    try:
        response = requests.post(
            url,
            timeout=10,
        )

    # ----------------------------------------
    # 연결 시간 초과
    # ----------------------------------------

    except requests.exceptions.ConnectTimeout:
        print("\n========================================")
        print("[EMAIL][API][TIMEOUT] Java 서버 연결 시간 초과")
        print("========================================")
        print(f"- 알림번호: {notification_no}")
        print(f"- Java 서버: {JAVA_BASE_URL}")
        print(f"- 요청 URL: {url}")
        print("- 원인: Java 서버에 연결하지 못했습니다.")
        print("- 확인: Java 서버 실행 여부")
        print("- 확인: 팀원 PC IP 주소")
        print("- 확인: 9102 포트")
        print("- 확인: 방화벽 및 네트워크 연결")
        print("========================================\n")

        return False

    # ----------------------------------------
    # 응답 시간 초과
    # ----------------------------------------

    except requests.exceptions.ReadTimeout:
        print("\n========================================")
        print("[EMAIL][API][READ_TIMEOUT] Java 서버 응답 시간 초과")
        print("========================================")
        print(f"- 알림번호: {notification_no}")
        print(f"- Java 서버: {JAVA_BASE_URL}")
        print("- 원인: Java 서버에는 연결되었지만 응답이 늦습니다.")
        print("- 확인: Java 이메일 발송 처리 / SMTP 연결")
        print("========================================\n")

        return False

    # ----------------------------------------
    # Java 서버 연결 실패
    # ----------------------------------------

    except requests.exceptions.ConnectionError as e:
        print("\n========================================")
        print("[EMAIL][API][CONNECTION] Java 서버 연결 실패")
        print("========================================")
        print(f"- 알림번호: {notification_no}")
        print(f"- Java 서버: {JAVA_BASE_URL}")
        print(f"- 요청 URL: {url}")
        print("- 원인: Java 서버에 연결할 수 없습니다.")
        print("- 확인: Java 서버 실행 여부")
        print("- 확인: 현재 Java 서버를 실행한 팀원 PC의 IP")
        print("- 확인: Java 서버 포트 9102")
        print(f"- 상세 오류: {e}")
        print("========================================\n")

        return False

    # ----------------------------------------
    # 기타 HTTP 요청 오류
    # ----------------------------------------

    except requests.RequestException as e:
        print("\n========================================")
        print("[EMAIL][API][REQUEST] 이메일 API 호출 오류")
        print("========================================")
        print(f"- 알림번호: {notification_no}")
        print(f"- 요청 URL: {url}")
        print(f"- 오류 타입: {type(e).__name__}")
        print(f"- 상세 오류: {e}")
        print("========================================\n")

        return False

    # ----------------------------------------
    # 이메일 발송 성공
    # ----------------------------------------

    if response.status_code == 200:
        print(
            f"[EMAIL][SUCCESS] 이메일 발송 성공 " f"(notification_no={notification_no})"
        )

        return True

    # ----------------------------------------
    # Java API 오류 응답
    # ----------------------------------------

    _print_email_error(
        notification_no=notification_no,
        status_code=response.status_code,
        response_text=response.text,
    )

    return False
