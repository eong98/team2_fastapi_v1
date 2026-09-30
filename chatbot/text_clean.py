"""
chatbot/text_clean.py

챗봇 답변용 텍스트 정리 공용 함수.
옵션메뉴 자동생성(chatbot/manual/)과 AI 상담(modules/chat_rag_langgraph.py)이 함께 씁니다.
"""

import re

_URL_RE = re.compile(r"https?://[^\s)\]>'\"]+|www\.[^\s)\]>'\"]+")
# /user/qa, /shopplan 같은 내부 경로 (앞이 단어/숫자/슬래시면 제외 → 24/7, and/or 보호)
_PATH_RE = re.compile(r"(?<![\w/.:])/[A-Za-z_][\w\-]*(?:/[\w\-{}:.]+)*/?")
_EMPTY_WRAP_RE = re.compile(r"\(\s*[,:]?\s*\)|\[\s*\]|`\s*`|<\s*>")
_DANGLING_LABEL_RE = re.compile(r"(경로|주소|링크|URL|url|페이지 주소)\s*[:：]\s*(?=[\n,.)]|$)")


# ── 사용자에게 보이면 안 되는 내부 운영 정보 ─────────────────────────
# 등급 번호 같은 내부 코드는 문장에서 지우고, 질문 자체가 내부 정보면 메뉴에서 뺀다.
# 새 항목이 생기면 아래 패턴에 추가하면 된다.

# 질문(메뉴 제목)이 이 패턴이면 사용자 메뉴에서 제외 (예: "회원 등급은 어떻게 나뉘나요?")
INTERNAL_QUESTION_RE = re.compile(
    r"회원\s*등급|등급\s*(은|이|체계|번호|코드|구분|종류)|관리자\s*(등급|로그인|계정|권한|전용)"
)
# 이 내용이 들어간 줄은 통째로 삭제 (예: "- 관리자 등급(1~5): 서비스 운영 관리자 계정입니다.")
_INTERNAL_LINE_RE = re.compile(r"관리자\s*등급|기본\s*등급은")
# 문장 안의 등급 번호 표기 제거
_INTERNAL_CODE_SUBS = [
    (re.compile(r"\s*\(\s*등급\s*\d+(?:\s*[~\-]\s*\d+)?\s*\)"), ""),          # 점주(등급 10) → 점주
    (re.compile(r"(등급)\s*\(\s*\d+(?:\s*[~\-]\s*\d+)?\s*\)"), r"\1"),        # 등급(6~8) → 등급
    (re.compile(r"(회원|점주|직원|관리자)\s*\(\s*\d+(?:\s*[~\-]\s*\d+)?\s*\)"), r"\1"),  # 일반 회원(6) → 일반 회원
    (re.compile(r"(등급)\s*\d+(?:\s*[~\-]\s*\d+)?"), r"\1"),                  # 등급 10으로 → 등급으로
]
# 메뉴 제목에서 내부 용어만 빼기 (예: "회원 등급 · 계정 상태" → "계정 상태")
_INTERNAL_LABEL_RE = re.compile(r"회원\s*등급\s*[·,/&]?\s*(및|와|과)?\s*")


def is_internal_question(label: str | None) -> bool:
    """사용자 메뉴에 보여주면 안 되는 내부 운영 질문인지."""
    return bool(INTERNAL_QUESTION_RE.search(label or ""))


def scrub_internal(text: str | None) -> str:
    """답변/문맥에서 등급 번호 같은 내부 운영 정보를 제거합니다."""
    if not text:
        return ""
    lines = [ln for ln in text.splitlines() if not _INTERNAL_LINE_RE.search(ln)]
    text = "\n".join(lines)
    for pattern, repl in _INTERNAL_CODE_SUBS:
        text = pattern.sub(repl, text)
    return text.strip()


def scrub_internal_label(label: str | None) -> str:
    """메뉴 제목에서 내부 용어를 뺍니다. 다 빠지면 원래 제목을 그대로 둡니다."""
    cleaned = _INTERNAL_LABEL_RE.sub("", label or "").strip(" ·,/")
    return cleaned or (label or "")


def strip_urls(text: str | None) -> str:
    """
    답변/문맥에서 URL과 내부 경로를 제거합니다.
    예) "구독권 안내 페이지(/shopplan)에서 확인" → "구독권 안내 페이지에서 확인"
    """
    if not text:
        return ""
    text = _URL_RE.sub("", text)
    text = _PATH_RE.sub("", text)
    text = _DANGLING_LABEL_RE.sub("", text)
    text = _EMPTY_WRAP_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.)])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
