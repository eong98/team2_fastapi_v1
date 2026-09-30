"""
AI 옵션생성 파이프라인 — 벡터화 → 섹션 분석 → 분류 → 하위메뉴 생성 → 제목 요약 → 저장.
"""

from chatbot.text_clean import scrub_internal_label
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.database import get_connection
from datetime import datetime

from chatbot.manual.category import _assign_categories, _load_existing_categories
from chatbot.manual.config import EMBED_MODEL, LLM_WORKERS, MAX_CHILDREN, STEP_LEAF, STEP_MID
from chatbot.manual.db import _cleanup_empty_categories, _delete_ai_menus_of_doc, _ensure_ano_column, _has_legacy_menus, _insert_menu
from chatbot.manual.docs import get_manual_docs
from chatbot.manual.markdown import _clean_label, _md_to_plain, _read_doc_structure
from chatbot.manual.progress import _Reporter, _new_job
from chatbot.manual.rag_topic import _generate_topic_tree, _plan_topics, _topic_context
from chatbot.manual.rules import _is_similar, _lob_str, _normalize_mid, _section_items, _shorten_long_labels, _top_guide
from chatbot.manual.vectors import _purge_empty_chunks, _add_doc_to_vectorstore, _ensure_embedding_model, _is_vectorized, _load_doc_chunks


def _build_section(sec: dict, doc_chunks: dict[int, list[str]]) -> dict | None:
    """섹션 하나 → STEP2 노드(하위 STEP3 질문 최대 5개). 목차형은 원문 그대로, 비구조형은 RAG+LLM."""
    if sec["kind"] == "md":
        raw = sec["sec"]
        mid = {"label": sec["label"], "answer": _md_to_plain(raw["intro"]) or None, "leaves": _section_items(raw)}
        return _normalize_mid(mid)

    topic = sec["topic"]
    tree = _generate_topic_tree(topic, _topic_context(sec["doc"]["no"], doc_chunks[sec["doc"]["no"]], topic))
    if not tree:
        return None
    # 3단계 트리를 STEP2(주제) → STEP3(질문) 2단계로 펼침
    leaves: list[dict] = []
    for m in tree["children"]:
        if m["leaves"]:
            leaves.extend(m["leaves"])
        else:
            leaves.append({"label": m["label"], "answer": m["answer"]})
    answer = tree["answer"] if tree["answer"] != _top_guide(tree["label"]) else None
    return _normalize_mid({"label": sec["label"], "answer": answer, "leaves": leaves[:MAX_CHILDREN]})


def _run_generation(rep: _Reporter) -> dict:
    """
    신규/수정 문서(UPDATEYN='N')와, 예전 방식(문서별 STEP1)으로 만들어진 문서를 대상으로
    섹션을 만들고 공용 카테고리(전체 최대 6개)에 배정해 저장합니다.
    진행률: 벡터화 0~10 / 섹션 분석 10~20 / 카테고리 이름(LLM)·배정(임베딩) 20~35 / 하위메뉴 생성 35~92 / 저장 92~100
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        _ensure_ano_column(cursor)
        legacy_nos = _has_legacy_menus(cursor)
    finally:
        cursor.close()
        connection.close()

    docs = [d for d in get_manual_docs() if d["updateYn"] == "N" or d["no"] in legacy_nos]
    if not docs:
        raise ValueError("새로 등록되거나 수정된 매뉴얼 문서가 없습니다.")
    legacy_cnt = sum(1 for d in docs if d["no"] in legacy_nos and d["updateYn"] == "Y")
    rep.step("target", f"대상 문서 {len(docs)}개 확인" + (f" (예전 구조 재생성 {legacy_cnt}개 포함)" if legacy_cnt else ""), 1)

    # 1) 벡터화 (0~10) — 업로드 때는 벡터화하지 않으므로 여기서 처리 (AI자유상담 검색 + 비구조 문서 RAG 재료)
    rep.step("embed", f"임베딩 모델({EMBED_MODEL}) 확인 중", 1)
    _ensure_embedding_model()
    rep.done("embed", f"임베딩 모델({EMBED_MODEL}) 확인 완료")
    rep._current = None
    purged = _purge_empty_chunks()
    if purged:
        rep.info("purge", f"벡터DB 정리: 본문이 비어 있는 항목 {purged}개 삭제 (AI 상담 검색 오류 원인)")
    vectorize_failed_docs = []
    for i, doc in enumerate(docs):
        key = f"vec-{doc['no']}"
        rep.step(key, f"[{doc['filename']}] 벡터화 확인 중", 1 + 9 * i / len(docs))
        if _is_vectorized(doc["no"]):
            rep.done(key, f"[{doc['filename']}] 이미 벡터화됨")
            rep._current = None
            continue
        rep.step(key, f"[{doc['filename']}] 벡터화 중 (AI자유상담 검색 반영)")
        try:
            _add_doc_to_vectorstore(doc["uploadPath"], doc["no"])
            rep.done(key, f"[{doc['filename']}] 벡터화 완료", 1 + 9 * (i + 1) / len(docs))
        except Exception:
            vectorize_failed_docs.append({"no": doc["no"], "filename": doc["filename"]})
            rep.fail(key, f"[{doc['filename']}] 벡터화 실패")
        rep._current = None

    # 2) 섹션 분석 (10~20) — 목차가 있으면 목차 섹션 그대로, 없으면 벡터DB 청크로 LLM이 주제 분석
    sections: list[dict] = []
    doc_chunks: dict[int, list[str]] = {}
    for i, doc in enumerate(docs):
        key = f"plan-{doc['no']}"
        pct = 10 + 10 * (i + 1) / len(docs)
        rep.step(key, f"[{doc['filename']}] 섹션 분석 중", 10 + 10 * i / len(docs))
        structure = _read_doc_structure(doc)
        if structure:
            for sec in structure:
                hint = " / ".join(_clean_label(it["title"]) for it in sec["items"][:3])
                sections.append({"doc": doc, "kind": "md", "sec": sec,
                                 "label": scrub_internal_label(_clean_label(sec["title"])), "hint": hint})
            rep.done(key, f"[{doc['filename']}] 목차 섹션 {len(structure)}개", pct)
        else:
            chunks = _load_doc_chunks(doc)
            if not chunks:
                rep.fail(key, f"[{doc['filename']}] 읽을 수 있는 내용이 없음", pct)
                rep._current = None
                continue
            doc_chunks[doc["no"]] = chunks
            topics = _plan_topics(doc, chunks)
            for t in topics:
                sections.append({"doc": doc, "kind": "rag", "topic": t, "label": t["label"], "hint": t["query"]})
            rep.done(key, f"[{doc['filename']}] AI 분석 주제 {len(topics)}개", pct)
        rep._current = None

    if not sections:
        raise ValueError("업로드된 문서에서 읽을 수 있는 내용이 없습니다.")
    target_nos = {s["doc"]["no"] for s in sections}

    # 3) 공용 카테고리 배정 (20~35) — 모든 대상 문서의 섹션을 한 번에 배정
    connection = get_connection()
    cursor = connection.cursor()
    try:
        existing = _load_existing_categories(cursor, target_nos)
    finally:
        cursor.close()
        connection.close()
    groups, dropped = _assign_categories(existing, sections, rep)
    names = ", ".join(f"{g['label']}({len(g['sections'])})" for g in groups)
    rep.done("assign", f"분류 완료: {names}", 35)
    rep._current = None
    if dropped:
        rep.info("dropped", f"안내: 메뉴당 하위메뉴 최대 {MAX_CHILDREN}개라 제외된 섹션 {len(dropped)}개 — {', '.join(s['label'] for s in dropped)}")

    # 4) 하위메뉴 생성 (35~92) — 목차형은 즉시, 질문 5개 초과/비구조형만 LLM
    jobs = [(g, sec) for g in groups for sec in g["sections"]]
    built: dict[int, dict] = {}  # id(sec) -> STEP2 트리
    failed_docs: set[int] = set()

    def work(order: int, sec: dict) -> dict | None:
        rep.start(f"sec-{order}", f"'{sec['label']}' 하위메뉴 생성 중")
        return _build_section(sec, doc_chunks)

    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        futures = {pool.submit(work, order, sec): (order, sec) for order, (_, sec) in enumerate(jobs)}
        for done_cnt, fut in enumerate(as_completed(futures), start=1):
            order, sec = futures[fut]
            pct = 35 + 57 * done_cnt / len(jobs)
            try:
                mid = fut.result()
                if mid:
                    built[id(sec)] = mid
                    rep.done(f"sec-{order}", f"'{sec['label']}' 완료 ({done_cnt}/{len(jobs)})", pct)
                else:
                    rep.done(f"sec-{order}", f"'{sec['label']}' 내용이 없어 건너뜀 ({done_cnt}/{len(jobs)})", pct)
            except Exception as e:
                failed_docs.add(sec["doc"]["no"])
                print(f"⚠ 섹션 생성 실패({sec['label']}): {e}")
                rep.fail(f"sec-{order}", f"'{sec['label']}' 생성 실패, 건너뜀 ({done_cnt}/{len(jobs)})", pct)

    # 5) 긴 메뉴 제목 요약 (말줄임 없이) → 저장 (92~100)
    rep.step("labels", "긴 메뉴 제목 요약 중", 92)
    shortened = _shorten_long_labels(list(built.values()))
    rep.done("labels", f"긴 메뉴 제목 {shortened}개 요약" if shortened else "요약할 긴 제목 없음")
    rep._current = None
    rep.step("save", "생성된 옵션 저장 중", 93)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 생성이 실패한 섹션이 있는 문서는 기존 메뉴를 지우지 않고 'N'으로 남겨 다음에 다시 생성
    replace_nos = target_nos - failed_docs
    preview_categories = []
    connection = get_connection()
    cursor = connection.cursor()
    try:
        for doc_no in replace_nos:
            _delete_ai_menus_of_doc(cursor, doc_no)

        for g in groups:
            mids = [(sec, built[id(sec)]) for sec in g["sections"]
                    if id(sec) in built and sec["doc"]["no"] in replace_nos]
            if not mids:
                continue

            cat_no = g["existingNo"]
            cursor.execute("SELECT LABEL, ANSWER FROM CHAT_MENU WHERE NO = :no", {"no": cat_no})
            row = cursor.fetchone()
            if row is None:
                continue  # 생성 도중 관리자가 그 최상위 메뉴를 지운 경우
            cat_label, cat_answer = row[0], _lob_str(row[1])

            # 최상위 메뉴와 제목이 사실상 같은 섹션은 하위메뉴로 두지 않고 STEP1 설명으로 사용
            # (그 섹션에 질문이 있으면 질문들을 STEP2로 한 단계 올림).
            # 관리자가 설명을 직접 적어둔 메뉴는 덮어쓰지 않는다.
            can_set_answer = not cat_answer or cat_answer == _top_guide(cat_label)
            overview, rest = None, []
            for sec, mid in mids:
                if overview is None and can_set_answer and _is_similar(mid["label"], cat_label):
                    if mid["answer"] and mid["answer"] != _top_guide(mid["label"]):
                        overview = mid["answer"]
                    rest.extend((sec, {"label": l["label"], "answer": l["answer"], "leaves": []}) for l in mid["leaves"])
                    continue
                rest.append((sec, mid))
            rest = rest[: max(0, MAX_CHILDREN - g["used"])]
            answer_only = not rest
            if overview:
                cat_answer = overview

            if overview:
                cursor.execute("UPDATE CHAT_MENU SET ANSWER = :answer WHERE NO = :no", {"answer": cat_answer, "no": cat_no})

            cursor.execute("SELECT NVL(MAX(VSEQ), 0) FROM CHAT_MENU WHERE PNO = :pno", {"pno": cat_no})
            mid_vseq = int(cursor.fetchone()[0])
            p_mids = []
            for sec, mid in rest:
                mid_vseq += 1
                doc_no = sec["doc"]["no"]
                mid_no = _insert_menu(cursor, pno=cat_no, step=STEP_MID, label=mid["label"], answer=mid["answer"],
                                      vseq=min(mid_vseq, 999), ano=doc_no, now=now)
                p_leaves = []
                for l_i, leaf in enumerate(mid["leaves"], start=1):
                    leaf_no = _insert_menu(cursor, pno=mid_no, step=STEP_LEAF, label=leaf["label"],
                                           answer=leaf["answer"], vseq=l_i, ano=doc_no, now=now)
                    p_leaves.append({"no": leaf_no, "label": leaf["label"], "answer": leaf["answer"]})
                p_mids.append({"no": mid_no, "label": mid["label"], "answer": mid["answer"],
                               "leaves": p_leaves, "filename": sec["doc"]["filename"]})
            preview_categories.append({"no": cat_no, "label": cat_label, "answer": cat_answer,
                                       "answerOnly": answer_only, "children": p_mids})

        _cleanup_empty_categories(cursor)
        for doc_no in replace_nos:
            cursor.execute("UPDATE ATTACH_MANUAL SET UPDATEYN = 'Y' WHERE NO = :no", {"no": doc_no})
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    skipped_docs = [d["filename"] for d in docs if d["no"] in failed_docs]
    summary = f"완료: 카테고리 {len(preview_categories)}개에 하위메뉴 {sum(len(c['children']) for c in preview_categories)}개 생성"
    if skipped_docs:
        summary += f" (일부 실패로 기존 메뉴 유지: {', '.join(skipped_docs)})"
    rep.step("finish", summary, 100)
    rep.done("finish")
    return {
        "categories": preview_categories,
        "vectorizeFailedDocs": vectorize_failed_docs,
        "skippedDocs": skipped_docs,
        "droppedSections": [f"{s['label']} ({s['doc']['filename']})" for s in dropped],
    }


def generate_menu_from_docs() -> dict:
    """동기 실행 버전(기존 API 호환용). 진행 상황은 기록만 하고 버립니다."""
    return _run_generation(_Reporter(_new_job()))
