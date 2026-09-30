"""
메뉴 트리 규칙 — 하위메뉴 합치기/5개 제한, 긴 제목 요약, 제목 유사도, 중요 질문 선택.
"""

import re

from chatbot.text_clean import is_internal_question, scrub_internal, scrub_internal_label, strip_urls
from difflib import SequenceMatcher
from langchain_core.messages import HumanMessage, SystemMessage

from chatbot.manual.config import LABEL_MAX_BYTES, LABEL_SUMMARY_BYTES, MAX_CHILDREN
from chatbot.manual.llm import structured_label_llm, structured_pick_llm
from chatbot.manual.markdown import _clean_label, _md_to_plain


# URL/내부 경로 제거는 AI 상담과 같은 공용 함수 사용
_strip_urls = strip_urls


_VAGUE_LABELS = {
    "기타", "정보", "일반", "안내", "기타문의", "기타안내", "기타정보", "기타사항", "일반정보", "기본정보", "그외",
    "도움말", "도움", "기능", "기능안내", "주요기능", "서비스", "서비스안내", "이용안내", "사용안내", "기본", "기본기능", "사용법",
}


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _is_similar(a: str, b: str) -> bool:
    """
    두 라벨이 사실상 같은 주제인지. 짧은 쪽이 긴 쪽에 포함되더라도 길이 차이가 크면
    (예: '공지사항' vs '공지사항은 어떻게 보나요') 하위 질문이므로 같은 주제로 보지 않는다.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    if short in long_ and len(short) / len(long_) >= 0.6:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.75


def _top_guide(label: str) -> str:
    return f"'{label}'에 관한 질문을 선택해주세요"


def _normalize_mid(mid: dict) -> dict | None:
    # 제목은 여기서 자르지 않는다 — 100바이트를 넘으면 저장 직전에 _shorten_long_labels가 요약
    label = scrub_internal_label(_strip_urls(mid.get("label")))
    answer = scrub_internal(_strip_urls(mid.get("answer")))
    if not label:
        return None

    leaves = []
    for leaf in mid.get("leaves") or []:
        l_label = _strip_urls(leaf.get("label"))
        l_answer = scrub_internal(_strip_urls(leaf.get("answer")))
        # 회원 등급 같은 내부 운영 질문은 사용자 메뉴에서 제외
        if not l_label or not l_answer or is_internal_question(l_label):
            continue
        # 부모(STEP2)와 사실상 같은 주제인 하위메뉴는 만들지 않고 답변만 부모로 흡수
        if _is_similar(l_label, label):
            answer = answer or l_answer
            continue
        if any(_is_similar(l_label, x["label"]) for x in leaves):
            continue
        leaves.append({"label": l_label, "answer": l_answer})

    # 하위메뉴가 1개뿐이면 굳이 한 단계 더 들어갈 필요가 없으므로 부모로 합침
    if len(leaves) == 1:
        only = leaves[0]["answer"]
        answer = f"{answer}\n\n{only}" if answer and answer != _top_guide(label) else only
        leaves = []

    leaves = leaves[:MAX_CHILDREN]

    if leaves:
        answer = answer or _top_guide(label)
    elif not answer:
        return None  # 하위도 답변도 없는 빈 메뉴는 만들지 않음

    return {"label": label, "answer": answer, "leaves": leaves}


def _normalize_top(top: dict) -> dict | None:
    label = _strip_urls(top.get("label"))
    if not label:
        return None
    top_answer = _strip_urls(top.get("answer")) or None
    if top_answer == _top_guide(label):
        top_answer = None

    mids: list[dict] = []
    for raw in top.get("children") or []:
        mid = _normalize_mid(raw)
        if mid is None:
            continue
        if _is_similar(mid["label"], label):
            if mid["leaves"]:
                # 부모와 같은 주제의 중간메뉴 → 그 하위를 한 단계 끌어올림
                mids.extend({"label": l["label"], "answer": l["answer"], "leaves": []} for l in mid["leaves"])
            else:
                # 부모와 같은 주제의 개요성 답변 → 최상위 답변으로 사용
                top_answer = f"{top_answer}\n\n{mid['answer']}" if top_answer else mid["answer"]
            continue
        dup = next((m for m in mids if _is_similar(m["label"], mid["label"])), None)
        if dup is not None:
            continue
        mids.append(mid)

    if len(mids) == 1:
        only = mids[0]
        if only["leaves"]:
            mids = [{"label": l["label"], "answer": l["answer"], "leaves": []} for l in only["leaves"]]
        else:
            top_answer = f"{top_answer}\n\n{only['answer']}" if top_answer else only["answer"]
            mids = []

    mids = mids[:MAX_CHILDREN]

    if mids:
        top_answer = top_answer or _top_guide(label)
    elif not top_answer:
        return None

    return {"label": label, "answer": top_answer, "children": mids}


def _fit_bytes(text: str, limit: int = 100) -> str:
    """LABEL은 VARCHAR2(100) BYTE라 한글(3바이트)은 33자 정도가 한계 — 바이트 기준으로 자른다."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[: limit - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def _rule_shorten(label: str) -> str:
    """LLM 요약이 실패했을 때: 첫 문장만 쓰고, 그래도 길면 따옴표 안 문구를 뺀다."""
    first = re.split(r"(?<=[.?!])\s+", label.strip())[0]
    if len(first.encode("utf-8")) > LABEL_SUMMARY_BYTES:
        first = re.sub(r"[\"“”'‘’][^\"“”'‘’]+[\"“”'‘’]\s*(라고|이라고)?\s*", "", first).strip() or first
    return first


def _shorten_long_labels(mids: list[dict]) -> int:
    """
    메뉴 제목이 길면(말줄임 "…"으로 잘리던 것) AI가 뜻을 유지한 채 짧게 요약합니다.
    요약한 메뉴는 답변 맨 앞에 원래 전체 질문("Q. ...")을 넣어, 클릭했을 때 전체 문구가 보이게 합니다.
    모든 긴 제목을 한 번의 LLM 호출로 처리하고, 실패하면 규칙(첫 문장 등)으로 줄입니다.
    반환: 요약한 제목 수
    """
    nodes = []
    for mid in mids:
        nodes.append(mid)
        nodes.extend(mid.get("leaves") or [])
    long_nodes = [n for n in nodes if len(n["label"].encode("utf-8")) > LABEL_SUMMARY_BYTES]
    if not long_nodes:
        return 0

    originals = [n["label"] for n in long_nodes]
    summarized: list[str] = []
    try:
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(originals))
        res = structured_label_llm.invoke([
            SystemMessage(content=f"""
고객상담 챗봇 선택지 버튼에 넣을 제목을 짧게 줄입니다.
아래 {len(originals)}개 제목을 각각 뜻이 바뀌지 않게 20자 안팎(최대 28자)으로 요약하세요.
- 질문 형태는 유지하세요 (예: "로그인이 안 돼요. \"아이디 또는 비밀번호가 일치하지 않습니다\"라고 나와요." → "로그인 시 아이디·비밀번호 불일치 오류")
- 순서와 개수를 그대로 지켜서 labels에 적으세요.

{numbered}
"""),
            HumanMessage(content="제목을 요약해주세요."),
        ])
        summarized = list(res.labels)
    except Exception as e:
        print(f"⚠ 긴 제목 요약 실패, 규칙으로 줄임: {e}")

    for i, node in enumerate(long_nodes):
        short = summarized[i].strip() if i < len(summarized) and summarized[i] else ""
        if not short or len(short.encode("utf-8")) > LABEL_MAX_BYTES:
            short = _rule_shorten(node["label"])
        full = node["label"]
        node["label"] = _fit_bytes(short)  # 마지막 안전장치 (여기까지 오면 거의 없음)
        node["answer"] = f"Q. {full}\n\n{node['answer']}" if node.get("answer") else f"Q. {full}"
    return len(long_nodes)


def _pick_important(topic_label: str, items: list[dict], k: int = MAX_CHILDREN) -> list[dict]:
    """질문이 5개를 넘을 때만 호출 — LLM이 번호만 고르므로 출력이 매우 짧아 빠르다."""
    numbered = "\n".join(f"[{i}] {it['label']}" for i, it in enumerate(items))
    system_prompt = f"""
고객상담 챗봇의 '{topic_label}' 메뉴에 넣을 질문을 고릅니다.
아래 질문 중 사용자가 가장 많이 궁금해할 핵심 질문을 최대 {k}개 골라 번호만 답하세요.
서로 내용이 겹치는 질문은 하나만 고르세요.

{numbered}
"""
    try:
        res = structured_pick_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content="번호를 골라주세요.")]
        )
        picks = sorted({i for i in res.picks if isinstance(i, int) and 0 <= i < len(items)})[:k]
        if picks:
            return [items[i] for i in picks]
    except Exception as e:
        print(f"⚠ 중요 질문 선택 실패, 앞에서부터 {k}개 사용: {e}")
    return items[:k]


def _section_items(sec: dict) -> list[dict]:
    """목차 섹션의 질문들 → STEP3 후보. 5개를 넘으면 LLM이 중요한 것만 고른다."""
    label = scrub_internal_label(_clean_label(sec["title"]))
    items = [{"label": _clean_label(it["title"]), "answer": scrub_internal(_md_to_plain(it["body"]))} for it in sec["items"]]
    # 회원 등급 같은 내부 운영 질문은 5개 선택 후보에서도 미리 제외
    items = [it for it in items if it["label"] and it["answer"] and not is_internal_question(it["label"])]
    if len(items) > MAX_CHILDREN:
        items = _pick_important(label, items)
    return items


def _lob_str(value) -> str:
    """Oracle CLOB(ANSWER)은 LOB 객체로 오므로 문자열로 변환."""
    if value is None:
        return ""
    return value.read() if hasattr(value, "read") else str(value)
