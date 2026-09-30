"""
매뉴얼(md) 목차 파싱과 본문 정리 — '## 섹션 / ### 질문' 구조를 메뉴 재료로 바꿈.
"""

import os
import re

from chatbot.text_clean import strip_urls


# 매뉴얼이 "## 섹션 → ### 질문 → 답변" 처럼 제목 구조를 갖고 있으면, 그 구조가
# 곧 메뉴 트리다. 이 경우 LLM에게 답변을 새로 쓰게 하지 않고 원문 답변을 그대로
# 정리해서 쓴다(CPU에서 가장 느린 "긴 답변 생성"을 통째로 생략 → 수 초 내 완료).
# LLM은 질문이 5개를 넘는 섹션에서 "중요한 5개 고르기"에만 짧게 쓴다.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


_LABEL_PREFIX_RE = re.compile(r"^\s*(?:Q\s*[.:)]\s*|\d+(?:\.\d+)*\s*[.)]\s*)", re.IGNORECASE)


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _md_to_plain(text: str) -> str:
    """마크다운 본문을 챗봇 답변용 평문으로 정리합니다(표 → 목록, 강조/구분선/URL 제거)."""
    lines = (text or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _table_cells(line)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _table_cells(lines[i])
                parts = [f"{h}: {v}" for h, v in zip(header[1:], cells[1:]) if v and v != "-"]
                out.append(f"- {cells[0]}" + (f" ({' / '.join(parts)})" if parts else ""))
                i += 1
            continue
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            i += 1
            continue
        out.append(line)
        i += 1
    plain = "\n".join(out)
    plain = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), plain)
    plain = plain.replace("`", "")
    return strip_urls(plain)


def _clean_label(title: str) -> str:
    label = _LABEL_PREFIX_RE.sub("", title or "")
    label = re.sub(r"\*\*|__|`", "", label)
    return strip_urls(label)


def _parse_md_structure(text: str) -> list[dict] | None:
    """
    제목 구조를 분석해서 [{title, intro, items:[{title, body}]}] 로 돌려줍니다.
    2번 이상 나오는 가장 얕은 제목 단계를 "주제", 그 바로 아래 단계를 "항목"으로 봅니다.
    제목 구조가 없으면 None → RAG + LLM 생성으로 처리.
    """
    lines = (text or "").splitlines()
    heads: list[tuple[int, int, str]] = []
    in_code = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else _HEADING_RE.match(line)
        if m:
            heads.append((idx, len(m.group(1)), m.group(2).strip()))
    if not heads:
        return None

    counts: dict[int, int] = {}
    for _, lvl, _ in heads:
        counts[lvl] = counts.get(lvl, 0) + 1
    topic_level = next((lvl for lvl in sorted(counts) if counts[lvl] >= 2), None)
    if topic_level is None:
        return None

    def body(k: int) -> str:
        start = heads[k][0] + 1
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        return "\n".join(lines[start:end])

    topics: list[dict] = []
    for k, (_, lvl, title) in enumerate(heads):
        if lvl < topic_level:
            continue  # 문서 제목(# ...) 등
        if lvl == topic_level:
            topics.append({"title": title, "intro": body(k), "items": []})
        elif topics:
            if lvl == topic_level + 1:
                topics[-1]["items"].append({"title": title, "body": body(k)})
            else:  # 더 깊은 제목은 직전 항목(없으면 주제 소개)에 이어붙임
                extra = f"{title}\n{body(k)}"
                if topics[-1]["items"]:
                    topics[-1]["items"][-1]["body"] += "\n" + extra
                else:
                    topics[-1]["intro"] += "\n" + extra
    topics = [t for t in topics if t["items"] or _md_to_plain(t["intro"])]
    return topics or None


def _read_doc_structure(doc: dict) -> list[dict] | None:
    path = doc.get("uploadPath")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_md_structure(f.read())
    except Exception as e:
        print(f"⚠ 문서 구조 분석 실패(문서 no={doc['no']}): {e}")
        return None
