from datetime import datetime

from core.database import get_connection
from modules.score import analyze_score
from modules.sentiment import analyze_sentiment
from modules.summary import analyze_summary


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