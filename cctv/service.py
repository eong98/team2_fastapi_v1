# -*- coding: utf-8 -*-
"""
cctv/service.py

Jetson이 확정한 이상행동 이벤트를 받아서:
1. code가 CCTV_ISSUE_CODE에 등록된 유효한 값인지 검증
2. LLM으로 detail -> comnet(관리자용 한국어 문장) 다듬기 (실패해도 저장은 계속 진행)
3. CCTV_ISSUE에 직접 INSERT (Spring REST API를 거치지 않음 - survey/service.py와 동일한 패턴)

PK(NO)는 Spring(JPA)이 쓰는 시퀀스 SEQ_CCTV_ISSUE_NO를 그대로 재사용한다.
"""

from datetime import datetime

from core.codes_cache import is_valid_code
from core.database import get_connection
from modules.cctv_issue import polish_detail_to_comnet


def report_issue(cno: int, code: str, detail: str, confidence: float) -> dict:
    if not is_valid_code(code):
        raise ValueError(f"등록되지 않았거나 사용 중지된 코드입니다: {code}")

    if confidence < 0 or confidence > 100:
        raise ValueError("confidence는 0~100 사이여야 합니다.")

    comnet = polish_detail_to_comnet(code, detail)
    reliability = f"{confidence:.0f}"  # 화면에서 formatReliability가 %를 붙여서 표시

    no = _insert_issue(cno=cno, code=code, comnet=comnet, reliability=reliability)

    return {
        "no": no,
        "code": code,
        "comnet": comnet,
        "reliability": reliability,
    }


def _insert_issue(cno: int, code: str, comnet: str, reliability: str) -> int:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO CCTV_ISSUE (
                NO, CNO, MNO, CODE, STATE, COMNET, RELIABILITY, PDATE, NOTICEYN, CDATE
            ) VALUES (
                SEQ_CCTV_ISSUE_NO.NEXTVAL, :cno, NULL, :code, 0, :comnet, :reliability, NULL, 'N', :cdate
            )
            """,
            cno=cno,
            code=code,
            comnet=comnet,
            reliability=reliability,
            cdate=now,
        )

        cursor.execute("SELECT SEQ_CCTV_ISSUE_NO.CURRVAL FROM DUAL")
        inserted_no = cursor.fetchone()[0]

        connection.commit()
        return int(inserted_no)

    finally:
        cursor.close()
        connection.close()
