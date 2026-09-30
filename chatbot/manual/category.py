"""
최상위 메뉴 — AI 추천, 관리자가 고른 목록으로 교체, 매뉴얼 섹션을 최상위 메뉴로 분류.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from core.database import get_connection
from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage

from chatbot.manual.config import AI_YN_MANUAL, LLM_WORKERS, MAX_CATEGORIES, MAX_CHILDREN, STEP_TOP, USEYN_HIDDEN
from chatbot.manual.db import _detach_menu_refs
from chatbot.manual.docs import get_manual_docs
from chatbot.manual.llm import structured_catname_llm, structured_classify_llm
from chatbot.manual.markdown import _clean_label, _read_doc_structure
from chatbot.manual.progress import _Reporter, _new_job
from chatbot.manual.rules import _VAGUE_LABELS, _fit_bytes, _is_similar, _lob_str, _norm, _pick_important, _strip_urls, _top_guide
from chatbot.manual.vectors import _get_chroma_embedding


def _load_existing_categories(cursor, target_nos: set[int]) -> list[dict]:
    """
    관리자가 직접 만든 최상위 메뉴(STEP1, AIYN='N')만 카테고리로 씁니다(최대 MAX_CATEGORIES개).
    used = 이번 생성 후에도 남는 하위 개수(대상 문서에서 나온 노드는 곧 지워지므로 제외).
    desc = 분류 참고용 설명(관리자가 적은 답변 + 이미 들어있는 하위메뉴 이름).
    """
    cursor.execute(
        "SELECT NO, LABEL, ANSWER FROM CHAT_MENU WHERE PNO IS NULL AND AIYN = :manual ORDER BY VSEQ, NO",
        {"manual": AI_YN_MANUAL},
    )
    tops = cursor.fetchall()
    cursor.execute(
        "SELECT PNO, ANO, LABEL FROM CHAT_MENU WHERE PNO IN (SELECT NO FROM CHAT_MENU WHERE PNO IS NULL)"
    )
    used: dict[int, int] = {}
    child_labels: dict[int, list[str]] = {}
    for pno, ano, label in cursor.fetchall():
        if ano is not None and ano in target_nos:
            continue
        used[pno] = used.get(pno, 0) + 1
        child_labels.setdefault(pno, []).append(label)

    result = []
    for no, label, answer in tops[:MAX_CATEGORIES]:
        answer = _lob_str(answer)
        desc_parts = []
        if answer and answer != _top_guide(label):
            desc_parts.append(_strip_urls(answer)[:150])
        if child_labels.get(no):
            desc_parts.append("포함: " + " / ".join(child_labels[no][:5]))
        result.append({"no": no, "label": label, "answer": answer, "desc": " · ".join(desc_parts), "used": used.get(no, 0)})
    return result


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _embedding_picks(cats: list[dict], sections: list[dict]) -> list[int]:
    """LLM 분류가 빠뜨린 섹션용 대체 — 임베딩(bge-m3) 유사도가 가장 높은 카테고리."""
    emb = _get_chroma_embedding()
    cat_vecs = emb.embed_documents([f"{c['label']}. {c['desc']}" for c in cats])
    sec_vecs = emb.embed_documents([f"{s['label']}. {s['hint']}" for s in sections])
    return [max(range(len(cats)), key=lambda j: _cosine(sv, cat_vecs[j])) for sv in sec_vecs]


def _classify_doc_sections(cats: list[dict], doc_sections: list[dict]) -> list[int]:
    """
    매뉴얼 하나의 섹션들을 관리자 카테고리 중 하나로 분류합니다.
    출력은 섹션 수만큼의 번호 목록이라 짧고, 매뉴얼 단위라 입력도 작아 로컬 CPU에서도 빠르다.
    번호가 비었거나 잘못되면 그 섹션만 임베딩 유사도로 채운다.
    """
    cat_lines = "\n".join(f"[{j}] {c['label']}" + (f" — {c['desc']}" if c["desc"] else "") for j, c in enumerate(cats))
    sec_lines = "\n".join(
        f"{i + 1}. {s['label']}" + (f" (질문: {s['hint']})" if s["hint"] else "") for i, s in enumerate(doc_sections)
    )
    system_prompt = f"""
고객상담 챗봇 메뉴를 정리합니다. 아래 매뉴얼 섹션 각각을 가장 알맞은 최상위 카테고리 하나에 분류하세요.

[카테고리]
{cat_lines}

[섹션 — 매뉴얼 '{doc_sections[0]['doc']['filename']}']
{sec_lines}

[규칙]
- picks에는 섹션 순서(1번부터 {len(doc_sections)}번까지)대로 카테고리 번호를 정확히 {len(doc_sections)}개 적으세요.
- 사용자가 그 섹션 내용을 찾으러 들어갈 카테고리를 고르세요.
- 어느 카테고리에도 맞지 않으면 -1을 적으세요.
"""
    picks: list[int] = []
    try:
        res = structured_classify_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content="섹션을 분류해주세요.")]
        )
        picks = list(res.picks)
    except Exception as e:
        print(f"⚠ 섹션 분류 실패, 임베딩으로 대체: {e}")

    picks = (picks + [None] * len(doc_sections))[: len(doc_sections)]
    missing = [i for i, p in enumerate(picks) if not isinstance(p, int) or not (-1 <= p < len(cats))]
    if missing:
        fallback = _embedding_picks(cats, [doc_sections[i] for i in missing])
        for i, j in zip(missing, fallback):
            picks[i] = j
    return picks


def suggest_categories(rep: "_Reporter | None" = None) -> dict:
    """
    [최상위 메뉴 AI 추천] — 누를 때마다 매뉴얼 전체 섹션을 보고 최상위 메뉴 후보를 처음부터 새로
    최대 MAX_CATEGORIES개 만듭니다(기존 메뉴 수와 무관). DB에는 저장하지 않습니다.
    관리자가 골라 [등록]하면 replace_top_menus()가 최상위 메뉴 전체를 그 목록으로 교체합니다.
    반환: { existing: [현재 관리자 최상위 메뉴 이름], max: 최대 개수,
            totalSections: 매뉴얼 섹션 수(메뉴당 최대 5개라 자리 계산용), suggestions: [{label, desc}] }
    """
    rep = rep or _Reporter(_new_job())
    rep.step("read", "등록된 최상위 메뉴와 매뉴얼 목차 읽는 중", 5)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        existing = _load_existing_categories(cursor, set())
    finally:
        cursor.close()
        connection.close()
    by_doc: list[str] = []
    total_sections = 0
    for doc in get_manual_docs():
        structure = _read_doc_structure(doc)
        if structure:
            total_sections += len(structure)
            by_doc.append(f"- {doc['filename']}: " + " / ".join(_clean_label(sec["title"]) for sec in structure))
    base = {"existing": [c["label"] for c in existing], "max": MAX_CATEGORIES, "totalSections": total_sections}
    rep.done("read", f"매뉴얼 {len(by_doc)}개 · 섹션 {total_sections}개 · 현재 최상위 메뉴 {len(existing)}개", 15)
    rep._current = None
    if not by_doc:
        raise ValueError("목차(## 제목)가 있는 매뉴얼이 없어 추천할 수 없습니다. 최상위 메뉴를 직접 등록해주세요.")

    existing_lines = "\n".join(f"- {c['label']}" for c in existing) or "(없음)"
    system_prompt = f"""
고객상담 챗봇 첫 화면에 나오는 최상위 메뉴 전체를 처음부터 새로 설계합니다. 최대 {MAX_CATEGORIES}개입니다.

[매뉴얼 섹션 목록]
{chr(10).join(by_doc)}

[현재 최상위 메뉴 — 참고용, 이름이 적절하면 그대로 써도 됨]
{existing_lines}

[규칙]
- 위 섹션들이 빠짐없이 들어갈 수 있도록 최상위 메뉴를 최대 {MAX_CATEGORIES}개 만드세요.
- 메뉴 하나에 섹션은 {MAX_CHILDREN}개까지만 들어갑니다. 섹션이 많은 매뉴얼은 2개 이상으로 나누세요.
  (예: '매장 관리'와 'CCTV 관리', '회원가입·로그인'과 '내 정보 관리', '고객센터'와 '문의사항')
- label은 10자 이내의 온전한 명사. '기타', '정보', '일반', '안내', '도움말' 같은 모호한 이름 금지.
- 서로 겹치는 이름(예: '매장 관리'와 '매장 · CCTV')을 만들지 마세요.
- desc는 사용자가 메뉴를 눌렀을 때 보여줄 한 문장 안내로 쓰세요.
"""
    rep.step("llm", f"AI가 최상위 메뉴 후보를 새로 만드는 중 (최대 {MAX_CATEGORIES}개)", 20)
    res = structured_catname_llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content="최상위 메뉴를 추천해주세요.")]
    )
    suggestions: list[dict] = []
    for c in res.categories:
        label = _fit_bytes(_clean_label(c.label))
        if not label or _norm(label) in _VAGUE_LABELS or any(_is_similar(label, x["label"]) for x in suggestions):
            continue
        suggestions.append({"label": label, "desc": _strip_urls(c.desc)})
    suggestions = suggestions[:MAX_CATEGORIES]
    rep.step("finish", f"추천 완료: {', '.join(x['label'] for x in suggestions) or '추천 없음'}", 100)
    rep.done("finish")
    return {**base, "suggestions": suggestions}


def replace_top_menus(menus: list[dict]) -> dict:
    """
    최상위 메뉴 전체를 관리자가 고른 목록(menus=[{label, desc}], 최대 MAX_CATEGORIES개)으로 교체합니다.
      - 이름이 같은 기존 관리자 메뉴: 그대로 유지(하위 포함), 설명(ANSWER)만 갱신
      - 목록에 없는 기존 최상위 메뉴(관리자/AI 모두): 하위까지 삭제
      - 새 이름: 비공개(USEYN='N')로 등록 — 하위메뉴를 [공개]할 때 함께 공개됨
      - 매뉴얼 전체 UPDATEYN='N' → [AI 옵션생성]으로 새 메뉴 구성에 맞게 다시 분류
    반환: { kept: [...], added: [...], removed: [...] }
    """
    cleaned = []
    for m in menus:
        label = _fit_bytes(_clean_label(m.get("label", "")))
        if label and not any(_norm(label) == _norm(x["label"]) for x in cleaned):
            cleaned.append({"label": label, "desc": (m.get("desc") or "").strip()})
    if not cleaned:
        raise ValueError("등록할 최상위 메뉴를 선택해주세요.")
    if len(cleaned) > MAX_CATEGORIES:
        raise ValueError(f"최상위 메뉴는 최대 {MAX_CATEGORIES}개까지 등록할 수 있습니다.")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kept, added, removed = [], [], []
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT NO, LABEL, AIYN FROM CHAT_MENU WHERE PNO IS NULL")
        roots = cursor.fetchall()

        keep_nos: set[int] = set()
        for vseq, m in enumerate(cleaned, start=1):
            match = next((r for r in roots if r[2] == AI_YN_MANUAL and _norm(r[1]) == _norm(m["label"])), None)
            answer = m["desc"] or _top_guide(m["label"])
            if match:
                keep_nos.add(match[0])
                kept.append(m["label"])
                cursor.execute(
                    "UPDATE CHAT_MENU SET ANSWER = :answer, VSEQ = :vseq WHERE NO = :no",
                    {"answer": answer, "vseq": vseq, "no": match[0]},
                )
            else:
                no_var = cursor.var(int)
                cursor.execute(
                    """
                    INSERT INTO CHAT_MENU (NO, PNO, STEP, LABEL, ANSWER, VSEQ, USEYN, AIYN, ANO, CDATE)
                    VALUES (CHAT_MENU_SEQ.NEXTVAL, NULL, :step, :label, :answer, :vseq, :useyn, :aiyn, NULL, :cdate)
                    RETURNING NO INTO :new_no
                    """,
                    {"step": STEP_TOP, "label": m["label"], "answer": answer, "vseq": vseq,
                     "useyn": USEYN_HIDDEN, "aiyn": AI_YN_MANUAL, "cdate": now, "new_no": no_var},
                )
                keep_nos.add(int(no_var.getvalue()[0]))
                added.append(m["label"])

        # 목록에 없는 최상위 메뉴는 하위까지 삭제 (자기참조 FK라 깊은 노드부터)
        for no, label, _ in roots:
            if no in keep_nos:
                continue
            cursor.execute(
                "SELECT NO, LEVEL FROM CHAT_MENU START WITH NO = :no CONNECT BY PRIOR NO = PNO",
                {"no": no},
            )
            nodes = sorted(cursor.fetchall(), key=lambda r: r[1], reverse=True)
            _detach_menu_refs(cursor, [n for n, _ in nodes])
            cursor.executemany("DELETE FROM CHAT_MENU WHERE NO = :1", [(n,) for n, _ in nodes])
            removed.append(label)

        # 메뉴 구성이 바뀌었으므로 매뉴얼 전체를 다시 분류 대상으로
        cursor.execute("UPDATE ATTACH_MANUAL SET UPDATEYN = 'N'")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
    return {"kept": kept, "added": added, "removed": removed}


def _assign_categories(existing: list[dict], sections: list[dict], rep: "_Reporter") -> tuple[list[dict], list[dict]]:
    """
    관리자가 만든 최상위 메뉴에 섹션을 배정합니다.
      1) 매뉴얼마다 LLM이 섹션 → 카테고리 번호 분류 (병렬)
      2) 카테고리마다 하위메뉴 최대 MAX_CHILDREN개 — 넘치면 LLM이 중요한 것만 남기고 나머지는 제외
    반환: (배정된 그룹 목록, 제외된 섹션 목록)
    """
    cats = existing
    if not cats:
        raise ValueError("최상위 메뉴가 없습니다. [+ 최상위 메뉴 추가]로 최상위 메뉴를 먼저 등록해주세요.")

    by_doc: dict[int, list[dict]] = {}
    for s in sections:
        by_doc.setdefault(s["doc"]["no"], []).append(s)

    rep.step("assign", f"섹션 {len(sections)}개를 최상위 메뉴 {len(cats)}개({', '.join(c['label'] for c in cats)})로 분류 중", 22)
    groups = [{"label": c["label"], "existingNo": c["no"], "used": c["used"], "sections": []} for c in cats]
    unmatched: list[dict] = []
    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        futures = {pool.submit(_classify_doc_sections, cats, secs): secs for secs in by_doc.values()}
        for fut in as_completed(futures):
            secs = futures[fut]
            for sec, j in zip(secs, fut.result()):
                (unmatched if j == -1 else groups[j]["sections"]).append(sec)

    # 원문 순서 유지
    order = {id(s): i for i, s in enumerate(sections)}
    for g in groups:
        g["sections"].sort(key=lambda s: order[id(s)])

    dropped = list(unmatched)
    trimmed = False
    for g in groups:
        room = max(0, MAX_CHILDREN - g["used"])
        if len(g["sections"]) <= room:
            continue
        if room == 0:
            dropped.extend(g["sections"])
            g["sections"] = []
            continue
        trimmed = True
        rep.step("trim", f"'{g['label']}' 하위메뉴가 {len(g['sections'])}개라 중요한 {room}개만 고르는 중", 32)
        picked = _pick_important(g["label"], [{"label": s["label"], "sec": s} for s in g["sections"]], room)
        keep = [p["sec"] for p in picked]
        dropped.extend(s for s in g["sections"] if not any(s is k for k in keep))
        g["sections"] = sorted(keep, key=lambda s: order[id(s)])
    if trimmed:
        rep.done("trim", "하위메뉴 5개 초과분 정리 완료")
    rep._current = None
    return [g for g in groups if g["sections"]], dropped
