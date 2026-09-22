import base64
import ssl
import time
import requests

from requests.adapters import HTTPAdapter

# ========================================
# 가비아 SMS 인증 정보
# ========================================

SMS_OAUTH_TOKEN_URL = "https://sms.gabia.com/oauth/token"

SMS_ID = "testcell2014sms"
API_KEY = "c014b2f02bb8e3c1f245a72856518778"


# ========================================
# Access Token 캐시
# ========================================

_cached_access_token = None
_token_expires_at = 0


# ========================================
# 가비아 SSL 호환 설정
# ========================================


class GabiaSSLAdapter(HTTPAdapter):

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()

        # 가비아 서버와 OpenSSL 3.x 호환용
        context.set_ciphers("DEFAULT:@SECLEVEL=1")

        kwargs["ssl_context"] = context

        return super().init_poolmanager(*args, **kwargs)


# ========================================
# 가비아 오류 원인 분석
# ========================================


def _print_token_error(status_code: int, response_text: str):
    """
    가비아 Access Token 발급 실패 원인을
    콘솔에서 확인하기 쉽게 출력한다.
    """

    print("\n========================================")
    print("[SMS][TOKEN][FAIL] Access Token 발급 실패")
    print("========================================")
    print(f"- HTTP 상태: {status_code}")

    response_lower = response_text.lower()

    if "api 발송 ip" in response_lower:
        print("- 원인: 가비아 API 발송 허용 IP가 등록되지 않았습니다.")
        print("- 확인: 가비아 관리툴의 API 발송 IP 설정")

    elif status_code == 401:
        print("- 원인: SMS_ID 또는 API_KEY 인증 실패 가능성이 있습니다.")
        print("- 확인: 가비아 SMS 계정 및 API Key")

    elif status_code == 403:
        print("- 원인: 가비아 API 접근 권한이 없습니다.")
        print("- 확인: API 사용 권한 또는 발송 IP 설정")

    elif status_code == 404:
        print("- 원인: 가비아 인증 API 주소를 찾을 수 없습니다.")
        print(f"- 확인 URL: {SMS_OAUTH_TOKEN_URL}")

    elif status_code == 429:
        print("- 원인: API 요청 횟수 제한 가능성이 있습니다.")

    elif status_code >= 500:
        print("- 원인: 가비아 인증 서버 오류 가능성이 있습니다.")

    else:
        print("- 원인: 가비아 인증 API 요청이 거부되었습니다.")

    print(f"- 가비아 응답: {response_text}")
    print("========================================\n")


# ========================================
# Access Token 발급 / 재사용
# ========================================


def get_access_token() -> str:
    """
    가비아 문자 API 인증용 Access Token을 반환한다.

    이미 발급받은 토큰이 유효하면 재사용하고,
    없거나 만료된 경우에만 새 토큰을 발급한다.
    """

    global _cached_access_token
    global _token_expires_at

    current_time = time.time()

    # ----------------------------------------
    # 기존 토큰 재사용
    # ----------------------------------------

    if _cached_access_token and current_time < _token_expires_at:
        print("[SMS][TOKEN][CACHE] 기존 Access Token 재사용")
        return _cached_access_token

    print("[SMS][TOKEN][START] 가비아 Access Token 발급 요청")

    # ----------------------------------------
    # SMS_ID + API_KEY Base64 인코딩
    # ----------------------------------------

    auth_string = f"{SMS_ID}:{API_KEY}"

    auth_value = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth_value}",
        "Content-Type": "application/x-www-form-urlencoded",
        "cache-control": "no-cache",
    }

    data = {
        "grant_type": "client_credentials",
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
    # Access Token 발급
    # ----------------------------------------

    try:
        response = session.post(
            SMS_OAUTH_TOKEN_URL,
            headers=headers,
            data=data,
            timeout=10,
            allow_redirects=False,
        )

    except requests.exceptions.ConnectTimeout:
        print("\n[SMS][TOKEN][TIMEOUT] 가비아 인증 서버 연결 시간 초과")
        print(f"- 요청 URL: {SMS_OAUTH_TOKEN_URL}")
        print("- 확인: H200 인터넷 연결 / 방화벽 / 가비아 서버 상태")
        raise Exception("가비아 인증 서버 연결 시간 초과")

    except requests.exceptions.ConnectionError as e:
        print("\n[SMS][TOKEN][CONNECTION] 가비아 인증 서버 연결 실패")
        print(f"- 요청 URL: {SMS_OAUTH_TOKEN_URL}")
        print(f"- 상세 오류: {e}")
        print("- 확인: 네트워크 / DNS / 방화벽")
        raise Exception("가비아 인증 서버 연결 실패")

    except requests.exceptions.SSLError as e:
        print("\n[SMS][TOKEN][SSL] 가비아 인증 서버 SSL 오류")
        print(f"- 상세 오류: {e}")
        print("- 확인: SSL/TLS 설정")
        raise Exception("가비아 인증 서버 SSL 오류")

    except requests.RequestException as e:
        print("\n[SMS][TOKEN][REQUEST] 가비아 인증 API 호출 오류")
        print(f"- 상세 오류: {e}")
        raise Exception("가비아 인증 API 호출 오류")

    # ----------------------------------------
    # HTTP 응답 확인
    # ----------------------------------------

    if response.status_code != 200:
        _print_token_error(
            response.status_code,
            response.text,
        )

        raise Exception(
            f"가비아 Access Token 발급 실패 " f"(HTTP {response.status_code})"
        )

    # ----------------------------------------
    # JSON 응답 처리
    # ----------------------------------------

    try:
        result = response.json()

    except ValueError:
        print("\n[SMS][TOKEN][PARSE] Access Token 응답 JSON 변환 실패")
        print(f"- 가비아 응답: {response.text}")
        raise Exception("Access Token 응답 형식 오류")

    access_token = result.get("access_token")

    if not access_token:
        print("\n[SMS][TOKEN][FAIL] Access Token이 응답에 없습니다.")
        print(f"- 가비아 응답: {result}")
        raise Exception("Access Token이 응답에 없습니다.")

    # ----------------------------------------
    # 토큰 유효시간 저장
    # ----------------------------------------

    try:
        expires_in = int(result.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600

    _cached_access_token = access_token

    # 만료 직전 사용을 피하기 위해 60초 여유
    _token_expires_at = current_time + max(expires_in - 60, 60)

    print("[SMS][TOKEN][SUCCESS] " f"Access Token 발급 성공 (유효시간={expires_in}초)")

    return _cached_access_token
