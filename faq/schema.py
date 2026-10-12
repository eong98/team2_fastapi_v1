from pydantic import BaseModel, Field

# ========================================
# FAQ AI 추천 요청 / 응답
# ========================================


class FaqSourceQa(BaseModel):
    """관리자 답변이 달린 1:1 문의 한 건"""

    no: int
    title: str = ""
    content: str = ""
    answer: str = ""


class FaqExisting(BaseModel):
    """이미 등록된 FAQ — 같은 내용을 또 추천하지 않기 위해 받음"""

    no: int
    title: str = ""
    content: str = ""


class FaqSuggestRequest(BaseModel):
    """
    FAQ AI 추천 요청 (Spring QaFaqSuggestClient)

    Spring이 관리자 확인·기간 필터를 끝낸 뒤 문의와 기존 FAQ를 넘기므로
    여기서는 DB를 읽지 않는다.
    """

    period: str = Field("", description="조회 기간 (예: 2026-07-14 ~ 2026-10-12)")
    questions: list[FaqSourceQa] = Field(default_factory=list, description="관리자 답변이 달린 1:1 문의")
    existing: list[FaqExisting] = Field(default_factory=list, description="이미 등록된 FAQ")


class FaqCandidate(BaseModel):
    """FAQ 후보 한 건 — 관리자가 확인·수정한 뒤 등록"""

    title: str          # 대표 질문 (FAQ 제목)
    content: str        # 질문 내용
    answer: str         # 정리된 답변
    type: int | None = None  # 문의 유형 (QA.TYPE 0~8) — AI가 내용으로 판단, 판단하지 못하면 None
    count: int          # 비슷한 문의 수
    sourceNos: list[int] = Field(default_factory=list)  # 근거가 된 문의 번호
    reason: str = ""    # 추천 이유 한 줄


class FaqSuggestResponse(BaseModel):
    """FAQ AI 추천 결과"""

    candidates: list[FaqCandidate] = Field(default_factory=list)
    analyzed: int = 0   # 분석한 문의 수
    groups: int = 0     # 비슷한 문의 묶음 수 (2건 이상)
    skipped: int = 0    # 이미 FAQ에 있어서 제외한 묶음 수
