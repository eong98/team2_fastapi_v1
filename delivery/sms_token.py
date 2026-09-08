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
    # 기존 토큰이 아직 유효하면 재사용
    # ----------------------------------------

    if (
        _cached_access_token
        and current_time < _token_expires_at
    ):
        return _cached_access_token

    # ----------------------------------------
    # SMS_ID + API_KEY Base64 인코딩
    # ----------------------------------------

    auth_string = f"{SMS_ID}:{API_KEY}"

    auth_value = base64.b64encode(
        auth_string.encode("utf-8")
    ).decode("utf-8")

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

    response = session.post(
        SMS_OAUTH_TOKEN_URL,
        headers=headers,
        data=data,
        timeout=10,
        allow_redirects=False,
    )

    if response.status_code != 200:
        raise Exception(
            f"가비아 Access Token 발급 실패 "
            f"(status={response.status_code}, "
            f"response={response.text})"
        )

    result = response.json()

    access_token = result.get("access_token")

    if not access_token:
        raise Exception(
            f"Access Token이 응답에 없습니다: {result}"
        )

    # ----------------------------------------
    # 토큰 유효시간 저장
    # ----------------------------------------

    expires_in = int(result.get("expires_in", 3600))

    _cached_access_token = access_token

    # 만료 직전 사용을 피하기 위해 60초 여유
    _token_expires_at = (
        current_time + max(expires_in - 60, 60)
    )

    return _cached_access_token