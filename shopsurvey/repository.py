"""
매장 설문 AI 자동작성 - 참고 자료 DB 조회

Spring이 점주 소유를 확인한 설문번호(svno)만 넘겨주므로 여기서는 권한 확인 없이 읽기만 한다.
- 이전 설문의 문항/보기 (SHOP_SURVEY + SHOP_SURVEY_QUESTION + SHOP_SURVEY_OPTION JOIN)
- 이전 설문의 AI 요약 약한 항목 (SHOP_SURVEY_SUMMARY JOIN SHOP_SURVEY)
"""

import json

from core.database import get_connection


# 한 번에 참고할 최대 설문 수 (프롬프트 길이 제한)
MAX_REFERENCES = 5


def load_references(svnos: list[int]) -> tuple[list[dict], list[str]]:
    """
    이전 설문 문항과 약한 항목을 조회한다.

    반환
    - references : [{"title": 설문제목, "questions": [{"title", "atype", "options": [보기...]}]}]
    - weak_points: ["매장 청결 - 점수 평균 4.2점", ...] (설문 여러 개면 합쳐서)
    """

    svnos = [int(n) for n in svnos if n is not None][:MAX_REFERENCES]
    if not svnos:
        return [], []

    binds = ", ".join(f":{i + 1}" for i in range(len(svnos)))

    connection = get_connection()
    cursor = connection.cursor()

    try:
        # 1. 설문 + 문항 + 보기
        cursor.execute(
            f"""
            SELECT S.NO, S.TITLE, Q.NO, Q.TITLE, Q.ATYPE, O.LABEL
            FROM SHOP_SURVEY S
            JOIN SHOP_SURVEY_QUESTION Q ON Q.SVNO = S.NO
            LEFT JOIN SHOP_SURVEY_OPTION O ON O.SQNO = Q.NO
            WHERE S.NO IN ({binds})
            ORDER BY S.NO, Q.SORT, Q.NO, O.SORT, O.NO
            """,
            svnos,
        )

        surveys: dict[int, dict] = {}
        questions: dict[int, dict] = {}

        for svno, s_title, sqno, q_title, atype, label in cursor.fetchall():
            survey = surveys.setdefault(svno, {"title": s_title, "questions": []})
            question = questions.get(sqno)
            if question is None:
                question = {"title": q_title, "atype": atype, "options": []}
                questions[sqno] = question
                survey["questions"].append(question)
            if label:
                question["options"].append(label)

        # 2. AI 요약 약한 항목
        cursor.execute(
            f"""
            SELECT SM.SVNO, SM.WEAKPOINTS
            FROM SHOP_SURVEY_SUMMARY SM
            JOIN SHOP_SURVEY S ON S.NO = SM.SVNO
            WHERE SM.SVNO IN ({binds})
            """,
            svnos,
        )

        weak_points: list[str] = []
        for _svno, weak in cursor.fetchall():
            if hasattr(weak, "read"):  # Oracle CLOB
                weak = weak.read()
            weak_points.extend(_parse_weak_points(weak))

        return list(surveys.values()), weak_points

    finally:
        cursor.close()
        connection.close()


def _parse_weak_points(value) -> list[str]:
    """WEAKPOINTS JSON 배열 문자열 → 문자열 목록 (형식이 깨져 있으면 무시)"""

    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]