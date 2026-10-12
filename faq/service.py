"""
faq/service.py

FAQ AI 추천 — 자주 들어오는 1:1 문의를 묶어 FAQ 후보를 만든다.

자동으로 등록하지 않는다. FAQ는 누구나 보는 공개 글인데 문의에는 개인 사정이 섞여 있고
AI가 답을 잘못 정리할 수도 있어서, 후보만 만들고 관리자가 확인·수정한 뒤 등록한다.

순서
  1. 문의(제목+내용)를 임베딩(bge-m3)으로 바꿔 뜻이 가까운 것끼리 묶는다
     — 표현이 달라도("해지하고 싶어요" / "구독 취소") 같은 질문으로 잡기 위해
  2. 2건 이상 모인 묶음만, 건수가 많은 순으로 남긴다
  3. 이미 등록된 FAQ와 뜻이 가까운 묶음은 뺀다
  4. 묶음마다 LLM이 대표 질문과 답변을 정리한다 (관리자가 실제로 단 답변만 근거로)

DB는 읽지 않는다(Spring이 넘겨준 데이터만 사용).
"""

import math

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import OllamaEmbeddings
from pydantic import BaseModel, Field

from core.llm_client import get_llm
from faq.schema import FaqSuggestRequest


# 생각(thinking) 모델은 답 전에 생각 텍스트를 길게 만들어 느리고 출력 한도에 걸리면 답이 비므로
# 가져온 LLM의 복사본에만 생각 끄기 (core/llm_client.py는 수정하지 않음)
llm = get_llm().model_copy(update={"reasoning": False})

# 같은 질문으로 보는 유사도 (코사인, 0~1) — 높일수록 묶음이 잘게 나뉨
# bge-m3로 재 보면 같은 주제의 문의끼리는 0.57~0.80, 다른 주제끼리는 0.54 이하로 나와서 그 사이로 잡음.
# 경계가 좁아 다른 질문이 섞일 수 있으므로, 묶은 뒤 LLM이 "같은 질문이 아니면 쓰지 않음"으로 한 번 더 거름.
SAME_QUESTION_SIMILARITY = 0.56
# 이미 있는 FAQ와 같은 내용으로 보는 유사도 (조금 더 엄격하게 — 비슷한 주제의 새 질문까지 빼지 않도록)
EXISTING_FAQ_SIMILARITY = 0.68
# FAQ 후보가 되려면 비슷한 문의가 최소 몇 건이어야 하는지
MIN_GROUP_SIZE = 2
# LLM으로 정리할 묶음 수 (묶음마다 LLM 1회 호출이라 H200 부담을 줄이려고 제한)
MAX_CANDIDATES = 5
# 묶음 하나에서 LLM에 보여 줄 문의 수 / 문의 하나의 글자 수
MAX_QA_PER_GROUP = 5
MAX_TEXT_LENGTH = 600
# 임베딩에 넣을 문의 글자 수
MAX_EMBED_LENGTH = 800
# 문의 유형 (QA.TYPE) — React QaType.ts의 QA_TYPE_MAP, modules/chat_summary.py의 유형 목록과 같은 값
QA_TYPES = {
    0: "기타",
    1: "관제신청",
    2: "영상요청",
    3: "장비장애",
    4: "구독권",
    5: "회원가입",
    6: "로그인",
    7: "CCTV",
    8: "직원초대",
}


class _FaqDraft(BaseModel):
    """LLM 응답 형식"""

    usable: bool = Field(
        description="FAQ로 올릴 만하면 true. 문의들이 서로 다른 질문이거나, 답변들이 서로 다르거나, 특정 고객 사정에만 해당하면 false"
    )
    title: str = Field(description="고객이 묻는 말투의 대표 질문 한 문장. 40자 이내, 물음표로 끝남. 예: 구독을 취소하려면 어떻게 하나요?")
    content: str = Field(description="고객 입장에서 질문을 풀어 쓴 1~2문장. 답은 여기에 쓰지 않음")
    answer: str = Field(description="고객에게 보여 줄 답변 전체. 관리자 답변들의 절차·조건을 빠짐없이 합쳐서 정리. 관리자 답변에 있는 내용만 사용")
    type: int = Field(0, description="문의 유형 숫자. 프롬프트의 [문의 유형] 중 질문 내용에 가장 맞는 것, 맞는 것이 없으면 0")
    reason: str = Field(description="추천 이유 한 문장 (40자 이내)")


structured_llm = llm.with_structured_output(_FaqDraft)

SYSTEM_PROMPT = (
    "당신은 고객센터에 들어온 1:1 문의를 읽고 자주 묻는 질문(FAQ) 초안을 만드는 AI입니다. "
    "반드시 요청된 JSON 형식으로만 응답하세요."
)


def suggest_faq(req: FaqSuggestRequest) -> dict:
    """자주 들어오는 문의를 묶어 FAQ 후보를 만든다."""

    questions = [q for q in req.questions if (q.title or q.content).strip() and q.answer.strip()]
    if len(questions) < MIN_GROUP_SIZE:
        raise ValueError("분석할 문의가 부족합니다. (관리자 답변이 달린 문의가 2건 이상 필요)")

    embedder = OllamaEmbeddings(model="bge-m3")

    # 1. 문의 임베딩 → 뜻이 가까운 것끼리 묶기
    vectors = [_normalize(v) for v in embedder.embed_documents([_embed_text(q.title, q.content) for q in questions])]
    groups = _group_by_similarity(vectors)

    # 2. 2건 이상인 묶음만, 큰 순서로
    groups = sorted((g for g in groups if len(g) >= MIN_GROUP_SIZE), key=len, reverse=True)
    group_count = len(groups)

    # 3. 이미 있는 FAQ와 겹치는 묶음 제외
    skipped = 0
    existing = [e for e in req.existing if (e.title or e.content).strip()]
    if existing and groups:
        existing_vectors = [
            _normalize(v) for v in embedder.embed_documents([_embed_text(e.title, e.content) for e in existing])
        ]
        kept = []
        for group in groups:
            # 묶음의 문의들과 기존 FAQ의 평균 유사도 — 하나라도 충분히 가까운 FAQ가 있으면 이미 있는 내용
            members = [vectors[i] for i in group]
            if any(sum(_dot(mv, ev) for mv in members) / len(members) >= EXISTING_FAQ_SIMILARITY for ev in existing_vectors):
                skipped += 1
            else:
                kept.append(group)
        groups = kept

    # 4. 묶음마다 대표 질문·답변 정리
    candidates = []
    for group in groups[:MAX_CANDIDATES]:
        members = [questions[i] for i in group]
        draft = _draft_faq(members)
        if draft is None:
            continue
        candidates.append(
            {
                "title": draft.title.strip(),
                "content": draft.content.strip(),
                "answer": draft.answer.strip(),
                "type": draft.type if draft.type in QA_TYPES else None,
                "count": len(members),
                "sourceNos": [m.no for m in members],
                "reason": draft.reason.strip(),
            }
        )

    print(
        f"[FAQ 추천] 문의 {len(questions)}건 → 묶음 {group_count}개 "
        f"(기존 FAQ와 겹쳐 제외 {skipped}) → 후보 {len(candidates)}개"
    )
    return {"candidates": candidates, "analyzed": len(questions), "groups": group_count, "skipped": skipped}


def _draft_faq(members) -> _FaqDraft | None:
    """묶음 하나를 FAQ 초안으로 — 쓸 만하지 않다고 판단하면 None"""
    lines = []
    for idx, m in enumerate(members[:MAX_QA_PER_GROUP], start=1):
        lines.append(
            f"[문의 {idx}]\n제목: {_cut(m.title)}\n내용: {_cut(m.content)}\n관리자 답변: {_cut(m.answer)}"
        )
    prompt = f"""
아래는 Allimio 서비스 고객센터에 들어온 비슷한 1:1 문의 {len(members)}건과 관리자가 단 답변입니다.
이 문의들을 대표하는 FAQ 한 건의 초안을 만드세요.

규칙
- answer에는 [관리자 답변]에 실제로 있는 내용만 쓰세요. 없는 절차·숫자·메뉴 이름을 지어내지 마세요.
- 이름, 이메일, 전화번호, 매장 이름, 주문번호 같은 개인·개별 정보는 넣지 마세요.
- 특정 고객에게만 해당하는 사정은 빼고, 누구에게나 해당하는 일반적인 질문과 답변으로 쓰세요.
- 문의들이 같은 질문이 아니거나, 관리자 답변들이 서로 다르거나, 일반적인 안내로 만들 수 없으면 usable=false 로 응답하세요.
- "따로 연락드리겠습니다"처럼 답이 아닌 답변뿐이면 usable=false 로 응답하세요.
- 고객에게 보여 줄 글이므로 존댓말로 쓰세요.
- title과 content는 "질문", answer는 "답"입니다. 답을 content에 쓰지 마세요.
- title은 고객이 묻는 문장으로 쓰고 물음표로 끝내세요. "~안내", "~방법"처럼 제목 형태로 쓰지 마세요.
- type은 아래 [문의 유형] 중 질문 내용에 가장 맞는 것의 숫자를 고르세요. 맞는 것이 없을 때만 0(기타)을 고르세요.

[문의 유형]
{chr(10).join(f"- {code}: {label}" for code, label in QA_TYPES.items())}

형식 예시 (내용은 따라 쓰지 말고 형식만 참고)
- title: 구독을 취소하려면 어떻게 하나요?
- content: 이용 중인 구독을 그만 쓰고 싶습니다. 취소하는 방법과 환불이 되는지 궁금합니다.
- answer: [전체 구독내역]에서 해당 구독의 [취소] 버튼을 누르시면 됩니다. 남은 기간은 일할 계산하여 환불해 드립니다.
- answer에는 관리자 답변들에 나온 방법(어느 화면에서 무엇을 누르는지)과 조건을 모두 담으세요.

{chr(10).join(lines)}
"""
    try:
        draft = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
    except Exception as e:  # 한 묶음이 실패해도 나머지는 계속
        print(f"[FAQ 추천] 초안 생성 실패(문의 {[m.no for m in members]}): {e}")
        return None
    if draft is None or not draft.usable or not draft.title.strip() or not draft.answer.strip():
        return None
    return draft


# ----------------------------------------
# 묶기 (외부 라이브러리 없이 단순 방식)
# ----------------------------------------


def _group_by_similarity(vectors: list[list[float]]) -> list[list[int]]:
    """
    뜻이 가까운 문의끼리 묶는다.
    문의를 하나씩 보면서, 지금까지 만든 묶음 중 "그 묶음에 든 문의들과의 평균 유사도"가 가장 높은 곳에 넣고
    충분히 가까운 묶음이 없으면 새 묶음을 만든다. (문의 수가 수백 건 수준이라 이 정도로 충분)

    묶음의 중심 벡터와 비교하면, 문의가 쌓일수록 중심이 두루뭉술해져 다른 주제의 문의까지 끌려 들어온다
    (구독 취소 묶음에 비밀번호 문의가 섞임). 그래서 묶음 안의 문의 하나하나와 비교한 평균을 쓴다.
    """
    groups: list[list[int]] = []
    for idx, vec in enumerate(vectors):
        best, best_sim = -1, SAME_QUESTION_SIMILARITY
        for g, members in enumerate(groups):
            sim = sum(_dot(vec, vectors[m]) for m in members) / len(members)
            if sim >= best_sim:
                best, best_sim = g, sim
        if best < 0:
            groups.append([idx])
        else:
            groups[best].append(idx)
    return groups


def _embed_text(title: str, content: str) -> str:
    return f"{(title or '').strip()}\n{(content or '').strip()}"[:MAX_EMBED_LENGTH]


def _cut(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= MAX_TEXT_LENGTH else text[:MAX_TEXT_LENGTH] + "…"


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
