import os
import smtplib

from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ========================================
# 환경변수 로드
# ========================================

load_dotenv()


# ========================================
# Gmail SMTP 설정
# ========================================

# 발송자 정보는 H200 .env에서 가져온다.
MAIL_HOST = os.getenv("MAIL_HOST", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")


# ========================================
# CCTV 알림 이메일 발송
# ========================================


def send_notification_email(
    to_email: str,
    title: str,
    content: str,
    image_url: str | None = None,
) -> tuple[bool, str]:
    """
    H200에서 Gmail SMTP를 이용하여
    회원 이메일로 CCTV 이슈 알림을 직접 발송한다.

    반환:
        성공:
            (True, "이메일 발송 완료")

        실패:
            (False, "실패 사유")

    발신자:
        .env의 MAIL_USERNAME

    수신자:
        MEMBER.EMAIL
    """

    # ----------------------------------------
    # 이메일 정보 확인
    # ----------------------------------------

    if not to_email or not str(to_email).strip():

        reason = "수신 이메일 주소가 없습니다."

        print(f"[EMAIL][FAIL] {reason}")
        print("- 확인: MEMBER.EMAIL")

        return False, reason

    if not MAIL_USERNAME or not MAIL_PASSWORD:

        reason = "SMTP 계정 설정이 없습니다."

        print(f"[EMAIL][FAIL] {reason}")
        print("- 확인: .env의 MAIL_USERNAME / MAIL_PASSWORD")

        return False, reason

    to_email = str(to_email).strip()

    print(f"[EMAIL][START] 이메일 발송 시작 " f"(to={to_email})")

    # ----------------------------------------
    # AI 이슈맵 HTML
    # ----------------------------------------

    image_html = ""

    if image_url:

        image_html = f"""
        <div style="margin-top:20px;">
            <p><strong>이슈 발생 위치</strong></p>

            <img
                src="{image_url}"
                alt="AI 이슈맵"
                style="max-width:600px; width:100%; height:auto;"
            />

            <p>
                <a href="{image_url}">
                    이슈 위치 이미지 보기
                </a>
            </p>
        </div>
        """

    # ----------------------------------------
    # 이메일 본문
    # ----------------------------------------

    html_content = f"""
    <div style="font-family:Arial,sans-serif; padding:20px;">

        <h2>{title}</h2>

        <p style="line-height:1.6;">
            {content}
        </p>

        {image_html}

    </div>
    """

    # ----------------------------------------
    # 이메일 메시지 생성
    # ----------------------------------------

    message = MIMEMultipart("alternative")

    message["From"] = MAIL_USERNAME
    message["To"] = to_email
    message["Subject"] = f"[Allimio] {title}"

    message.attach(
        MIMEText(
            html_content,
            "html",
            "utf-8",
        )
    )

    # ----------------------------------------
    # Gmail SMTP 발송
    # ----------------------------------------

    try:

        print(f"[EMAIL][SMTP] " f"{MAIL_HOST}:{MAIL_PORT} 연결")

        with smtplib.SMTP(
            MAIL_HOST,
            MAIL_PORT,
            timeout=15,
        ) as smtp:

            # TLS 보안 연결
            smtp.starttls()

            # 관리자 Gmail 로그인
            smtp.login(
                MAIL_USERNAME,
                MAIL_PASSWORD,
            )

            # 회원 이메일로 발송
            smtp.sendmail(
                MAIL_USERNAME,
                [to_email],
                message.as_string(),
            )

        reason = "이메일 발송 완료"

        print(f"[EMAIL][SUCCESS] " f"{reason} " f"(to={to_email})")

        return True, reason

    # ----------------------------------------
    # Gmail 인증 실패
    # ----------------------------------------

    except smtplib.SMTPAuthenticationError as e:

        reason = "Gmail SMTP 인증 실패"

        print(f"[EMAIL][AUTH_FAIL] {reason}")
        print("- 확인: MAIL_USERNAME")
        print("- 확인: Google 앱 비밀번호")
        print(f"- 상세 오류: {e}")

        return False, reason

    # ----------------------------------------
    # 수신자 거부
    # ----------------------------------------

    except smtplib.SMTPRecipientsRefused as e:

        reason = "수신 이메일 주소가 거부되었습니다."

        print(f"[EMAIL][RECIPIENT_FAIL] {reason}")
        print(f"- 수신자: {to_email}")
        print(f"- 상세 오류: {e}")

        return False, reason

    # ----------------------------------------
    # 발신자 거부
    # ----------------------------------------

    except smtplib.SMTPSenderRefused as e:

        reason = "SMTP 서버에서 발신자 주소를 거부했습니다."

        print(f"[EMAIL][SENDER_FAIL] {reason}")
        print(f"- 상세 오류: {e}")

        return False, reason

    # ----------------------------------------
    # SMTP 연결 실패
    # ----------------------------------------

    except smtplib.SMTPConnectError as e:

        reason = "Gmail SMTP 서버 연결에 실패했습니다."

        print(f"[EMAIL][CONNECT_FAIL] {reason}")
        print(f"- 서버: {MAIL_HOST}:{MAIL_PORT}")
        print(f"- 상세 오류: {e}")

        return False, reason

    # ----------------------------------------
    # SMTP 서버 오류
    # ----------------------------------------

    except smtplib.SMTPException as e:

        reason = f"SMTP 발송 오류: {str(e)}"

        print("[EMAIL][SMTP_FAIL] 이메일 발송 오류")
        print(f"- 수신자: {to_email}")
        print(f"- 상세 오류: {e}")

        return False, reason

    # ----------------------------------------
    # 기타 오류
    # ----------------------------------------

    except Exception as e:

        reason = f"이메일 발송 오류: " f"{type(e).__name__}: {str(e)}"

        print("[EMAIL][FAIL] 이메일 발송 오류")
        print(f"- 수신자: {to_email}")
        print(f"- 오류 타입: {type(e).__name__}")
        print(f"- 상세 오류: {e}")

        return False, reason
