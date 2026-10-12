"""
chatbot/ticket.py

챗봇 연결용 티켓 검증.

FastAPI는 Spring의 로그인 토큰(HttpOnly 쿠키)을 볼 수 없어서, 예전에는 화면이 보내는
회원번호(mno)를 그대로 믿었습니다. 번호만 바꾸면 다른 회원의 실시간 알림을 받을 수 있었습니다.

이제 Spring이 요청자를 확인(회원: 로그인 토큰, 비회원: 브라우저 식별값)한 뒤
짧은 수명의 티켓을 발급하고, FastAPI는 그 티켓의 서명만 검증합니다.

  티켓 = HS256 JWT  { "sub": "m:회원번호" | "g:비회원식별값" | "a:관리자번호" | "server", "typ": "chat", "exp": 만료시각 }
  발급: Spring ChatTicketProvider (GET /chat_session/ticket)
  전달: HTTP는 X-Chat-Ticket 헤더, WebSocket은 ?ticket= 파라미터

두 서버가 같은 비밀키(환경변수 CHAT_TICKET_SECRET, 32자 이상)를 가질 때만 검사가 켜집니다.
키가 없으면 검사를 건너뛰고 예전처럼 동작합니다 — 한쪽 서버만 먼저 올려도 기존 기능이 깨지지 않게.
"""

import base64
import hashlib
import hmac
import json
import os
import time

from dotenv import load_dotenv
from fastapi import Header, HTTPException

from chatbot.chat_repository import get_session_owner

load_dotenv()

TICKET_HEADER = "X-Chat-Ticket"
TICKET_TYPE = "chat"
SERVER_SUBJECT = "server"  # Spring 서버가 직접 부를 때 (상담 제목 요약, 문의 AI 답변)


def _secret() -> bytes:
    return (os.getenv("CHAT_TICKET_SECRET") or "").strip().encode("utf-8")


def ticket_enabled() -> bool:
    """비밀키가 설정돼 있으면(32바이트 이상) 티켓 검사를 켭니다."""
    return len(_secret()) >= 32


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_ticket(ticket: str | None) -> str | None:
    """
    티켓이 유효하면 주인(sub: "m:1", "g:uuid", "server")을, 아니면 None을 돌려줍니다.
    (서명 불일치·만료·형식 오류·다른 용도의 토큰은 모두 None)
    """
    if not ticket:
        return None
    try:
        header_b64, payload_b64, signature_b64 = ticket.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            return None

        expected = hmac.new(_secret(), f"{header_b64}.{payload_b64}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            return None

        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("typ") != TICKET_TYPE:
            return None
        if float(payload.get("exp", 0)) <= time.time():
            return None

        subject = payload.get("sub")
        return subject if isinstance(subject, str) and subject else None
    except Exception:
        return None


def owner_subject(mno, gno) -> str | None:
    """상담 주인을 티켓의 sub와 같은 형식으로 — 회원 "m:번호", 비회원 "g:식별값" """
    if mno:
        return f"m:{mno}"
    if gno:
        return f"g:{gno}"
    return None


def require_session_access(sno: str, x_chat_ticket: str | None = Header(default=None, alias=TICKET_HEADER)):
    """
    상담방 API용 — 티켓 주인이 이 상담방의 주인인지 확인합니다. (FastAPI Depends로 사용)
    - 티켓 없음·만료 → 401 (화면이 티켓을 새로 받아 다시 요청)
    - 남의 상담방   → 403
    - Spring 서버 티켓(server)은 모든 상담방 통과
    """
    if not ticket_enabled():
        return
    subject = verify_ticket(x_chat_ticket)
    if subject is None:
        raise HTTPException(status_code=401, detail="상담 연결 확인이 만료되었습니다. 다시 시도해 주세요.")
    if subject == SERVER_SUBJECT:
        return
    mno, gno = get_session_owner(sno)
    if subject != owner_subject(mno, gno):
        raise HTTPException(status_code=403, detail="본인의 상담만 이용할 수 있습니다.")


def require_admin(x_chat_ticket: str | None = Header(default=None, alias=TICKET_HEADER)):
    """
    관리자 화면 전용 API용 (매뉴얼 업로드·삭제, 옵션메뉴 생성 등 /api/chatbot/manual-doc/*).
    Spring이 관리자 로그인(ROLE_MANAGER)을 확인하고 발급한 관리자 티켓("a:관리자번호")이 있어야 합니다.
    - 티켓 없음·만료 → 401 (화면이 티켓을 새로 받아 다시 요청)
    - 회원·비회원 티켓 → 403
    """
    if not ticket_enabled():
        return
    subject = verify_ticket(x_chat_ticket)
    if subject is None:
        raise HTTPException(status_code=401, detail="관리자 확인이 만료되었습니다. 다시 시도해 주세요.")
    if subject != SERVER_SUBJECT and not subject.startswith("a:"):
        raise HTTPException(status_code=403, detail="관리자만 이용할 수 있습니다.")


def require_server(x_chat_ticket: str | None = Header(default=None, alias=TICKET_HEADER)):
    """Spring 서버만 부를 수 있는 API용 (문의 AI 자동 답변 등)."""
    if not ticket_enabled():
        return
    if verify_ticket(x_chat_ticket) != SERVER_SUBJECT:
        raise HTTPException(status_code=401, detail="서버 전용 API입니다.")
