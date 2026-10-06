from datetime import datetime

from core.database import get_connection
from modules.score import analyze_score
from modules.sentiment import analyze_sentiment
from modules.summary import analyze_summary


# ========================================
# 1. 설문 전체 응답 조회
# ========================================

def get_survey_answers(survey_no: int) -> list[dict]:
    """
    특정 설문의 모든 회원 응답을 Oracle DB에서 조회한다.

    AI 모듈이 사용할 수 있도록
    질문 유형, 질문 내용, 실제 답변 형태로 반환한다.
    """

    connection = get_connection()
    cursor = connection.cursor()

    try:
        sql = """
            SELECT
                SR.NO,
                SR.MNO,
                SQ.NO,
                SQ.QTEXT,
                SQ.QTYPE,
                SA.ATEXT
            FROM SURVEYRESPONSE SR
            JOIN SURVEYANSWER SA
                ON SA.RNO = SR.NO
            JOIN SURVEYQUESTION SQ
                ON SQ.NO = SA.QNO
            WHERE SR.SVNO = :survey_no
            ORDER BY SR.NO, SQ.SEQNO
        """

        cursor.execute(
            sql,
            survey_no=survey_no
        )

        rows = cursor.fetchall()

        data = []

        for row in rows:
            answer = row[5]

            # Oracle CLOB 처리
            if hasattr(answer, "read"):
                answer = answer.read()

            data.append({
                "responseNo": row[0],
                "memberNo": row[1],
                "questionNo": row[2],
                "question": row[3],
                "type": row[4],
                "answer": answer
            })

        return data

    finally:
        cursor.close()
        connection.close()


# ========================================
# 2. 설문 AI 분석 실행
# ========================================

def analyze_survey(survey_no: int) -> dict:
    """
    특정 설문의 전체 응답을 조회하고
    공용 AI 모듈을 이용하여 종합 분석한다.
    """

    data = get_survey_answers(survey_no)

    if not data:
        raise ValueError(
            f"설문번호 {survey_no}의 응답 데이터가 없습니다."
        )

    # AI 종합 점수
    ai_score = analyze_score(data)

    # 감정 분석
    sentiment = analyze_sentiment(data)

    # 요약 분석
    summary = analyze_summary(data)

    result = {
        "surveyNo": survey_no,

        "aiScore": ai_score,

        "positiveRate": sentiment["positiveRate"],
        "neutralRate": sentiment["neutralRate"],
        "negativeRate": sentiment["negativeRate"],

        "summary": summary["summary"],
        "positiveSummary": summary["positiveSummary"],
        "negativeSummary": summary["negativeSummary"]
    }

    save_analysis(result)

    return result


# ========================================
# 3. AI 분석 결과 저장
# ========================================

def save_analysis(result: dict):
    """
    AI 분석 결과를 SURVEYANALYSIS에 저장한다.

    동일한 설문의 기존 분석 결과가 있으면 UPDATE,
    없으면 INSERT 한다.
    """

    connection = get_connection()
    cursor = connection.cursor()

    try:
        survey_no = result["surveyNo"]

        cursor.execute(
            """
            SELECT NO
            FROM SURVEYANALYSIS
            WHERE SVNO = :survey_no
            """,
            survey_no=survey_no
        )

        existing = cursor.fetchone()

        cdate = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # ----------------------------------------
        # 기존 분석 결과가 있으면 UPDATE
        # ----------------------------------------

        if existing:

            cursor.execute(
                """
                UPDATE SURVEYANALYSIS
                SET
                    AISCORE = :ai_score,
                    POSITIVE_RATE = :positive_rate,
                    NEUTRAL_RATE = :neutral_rate,
                    NEGATIVE_RATE = :negative_rate,
                    SUMMARY = :summary,
                    POSITIVE_SUMMARY = :positive_summary,
                    NEGATIVE_SUMMARY = :negative_summary,
                    CDATE = :cdate
                WHERE SVNO = :survey_no
                """,
                ai_score=result["aiScore"],
                positive_rate=result["positiveRate"],
                neutral_rate=result["neutralRate"],
                negative_rate=result["negativeRate"],
                summary=result["summary"],
                positive_summary=result["positiveSummary"],
                negative_summary=result["negativeSummary"],
                cdate=cdate,
                survey_no=survey_no
            )

        # ----------------------------------------
        # 기존 분석 결과가 없으면 INSERT
        # ----------------------------------------

        else:

            cursor.execute(
                """
                INSERT INTO SURVEYANALYSIS (
                    NO,
                    SVNO,
                    AISCORE,
                    POSITIVE_RATE,
                    NEUTRAL_RATE,
                    NEGATIVE_RATE,
                    SUMMARY,
                    POSITIVE_SUMMARY,
                    NEGATIVE_SUMMARY,
                    CDATE
                )
                VALUES (
                    SURVEYANALYSIS_SEQ.NEXTVAL,
                    :survey_no,
                    :ai_score,
                    :positive_rate,
                    :neutral_rate,
                    :negative_rate,
                    :summary,
                    :positive_summary,
                    :negative_summary,
                    :cdate
                )
                """,
                survey_no=survey_no,
                ai_score=result["aiScore"],
                positive_rate=result["positiveRate"],
                neutral_rate=result["neutralRate"],
                negative_rate=result["negativeRate"],
                summary=result["summary"],
                positive_summary=result["positiveSummary"],
                negative_summary=result["negativeSummary"],
                cdate=cdate
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


# ========================================
# 4. 기존 AI 분석 결과 조회
# ========================================

def get_survey_analysis(survey_no: int) -> dict | None:
    """
    SURVEYANALYSIS에 저장된 기존 AI 분석 결과를 조회한다.

    분석 결과가 아직 없는 경우
    오류를 발생시키지 않고 None을 반환한다.
    """

    connection = get_connection()
    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            SELECT
                NO,
                SVNO,
                AISCORE,
                POSITIVE_RATE,
                NEUTRAL_RATE,
                NEGATIVE_RATE,
                SUMMARY,
                POSITIVE_SUMMARY,
                NEGATIVE_SUMMARY,
                CDATE
            FROM SURVEYANALYSIS
            WHERE SVNO = :survey_no
            """,
            survey_no=survey_no
        )

        row = cursor.fetchone()

        # ----------------------------------------
        # 분석 결과 없음
        # ----------------------------------------

        if row is None:

            print(
                f"[SURVEY][ANALYSIS][NOT_FOUND] "
                f"저장된 AI 분석 결과 없음 "
                f"(survey_no={survey_no})"
            )

            return None

        # ----------------------------------------
        # Oracle CLOB 처리
        # ----------------------------------------

        summary = row[6]
        positive_summary = row[7]
        negative_summary = row[8]

        if hasattr(summary, "read"):
            summary = summary.read()

        if hasattr(positive_summary, "read"):
            positive_summary = positive_summary.read()

        if hasattr(negative_summary, "read"):
            negative_summary = negative_summary.read()

        result = {
            "surveyNo": int(row[1]),
            "aiScore": float(row[2]),

            "positiveRate": float(row[3]),
            "neutralRate": float(row[4]),
            "negativeRate": float(row[5]),

            "summary": summary or "",
            "positiveSummary": positive_summary or "",
            "negativeSummary": negative_summary or ""
        }

        print(
            f"[SURVEY][ANALYSIS][SUCCESS] "
            f"기존 AI 분석 결과 조회 성공 "
            f"(survey_no={survey_no})"
        )

        return result

    except Exception as e:

        print(
            f"[SURVEY][ANALYSIS][FAIL] "
            f"기존 AI 분석 결과 조회 실패 "
            f"(survey_no={survey_no})"
        )

        print(f"- 상세 오류: {e}")

        raise

    finally:
        cursor.close()
        connection.close()