from core.database import get_connection
from modules.translation import translate_notification
from shopmap.service import create_issue_map

VALID_PRIORITIES = {"LOW", "NORMAL", "HIGH", "EMERGENCY"}


def save_notification(
    cino,
    mno,
    atitle,
    content,
    priority,
    lang,
    little,
    field,
    aimapno=None,
):
    """NOTIFICATION 저장 후 알림 번호 반환."""

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT SEQ_NOTIFICATION_NO.NEXTVAL FROM DUAL")
        no = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO NOTIFICATION (
                NO,
                CINO,
                MNO,
                AIMAPNO,
                AUDIONO,
                ATITLE,
                CONTENT,
                PRIORITY,
                STATUS,
                CDATE,
                LANG,
                LITTLE,
                FIELD,
                READYN
            )
            VALUES (
                :no,
                :cino,
                :mno,
                :aimapno,
                NULL,
                :atitle,
                :content,
                :priority,
                'READY',
                TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI:SS'),
                :lang,
                :little,
                :field,
                'N'
            )
        """,
            {
                "no": no,
                "cino": cino,
                "mno": mno,
                "aimapno": aimapno,
                "atitle": atitle,
                "content": content,
                "priority": priority,
                "lang": lang,
                "little": little,
                "field": field,
            },
        )

        conn.commit()
        return no

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def create_notification(
    cino,
    mno,
    shopmapno,
    issue,
    xpos=None,
    ypos=None,
    lang=None,
    priority="NORMAL",
):
    """AI 이슈맵 호출 -> 번역 -> NOTIFICATION 저장."""

    priority = str(priority).upper()
    if priority not in VALID_PRIORITIES:
        priority = "NORMAL"

    # 1. AI 이슈맵 생성/저장
    map_result = create_issue_map(
        shopmapno=shopmapno,
        issue=issue,
        xpos=xpos,
        ypos=ypos,
    )

    # 성공한 이슈맵만 알림에 연결
    aimapno = map_result.get("aimapno") if map_result.get("success") else None

    # 2. 원본 알림
    atitle = "CCTV 이슈 알림"
    content = issue

    # 3. 번역 - 실패해도 원본 알림 저장은 계속
    little, field = translate_notification(
        atitle,
        content,
        lang,
    )

    # 4. 알림 저장
    no = save_notification(
        cino=cino,
        mno=mno,
        atitle=atitle,
        content=content,
        priority=priority,
        lang=lang,
        little=little,
        field=field,
        aimapno=aimapno,
    )

    return {
        "no": no,
        "cino": cino,
        "mno": mno,
        "aimapno": aimapno,
        "status": "READY",
        "mapStatus": map_result.get("status", 2),
        "translated": little is not None or field is not None,
    }
