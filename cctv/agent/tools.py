# -*- coding: utf-8 -*-
"""
cctv/agent/tools.py

CCTV 이슈 AI 검토 에이전트가 쓰는 "조회 도구" 모음.

[설계 원칙]
1. 전부 SELECT만 한다. 에이전트는 DB를 바꾸지 못한다(정탐/오탐 확정은 사람이 버튼으로 한다).
2. 어느 매장·어느 CCTV·어느 이슈를 볼지는 서버가 IssueContext로 고정한다.
   LLM이 정할 수 있는 건 "어떤 도구를 쓸지"와 조회 범위(days, minutes, scope)뿐이다.
   -> LLM이 번호를 지어내도 다른 매장 데이터를 조회할 수 없다.
3. 도구는 {"summary": 한국어 한 줄, "data": 숫자/목록} 을 돌려준다.
   summary는 LLM이 아니라 이 코드가 만든 문장이라, 화면의 "조회한 근거"에 그대로 보여줘도
   지어낸 내용이 섞이지 않는다.

날짜 컬럼(CDATE, INTIME, OUTTIME, SDATE, EDATE)은 전부 'YYYY-MM-DD HH:MM:SS' 문자열이라
문자열 비교(>=, <=)가 곧 시간 비교다. 그래서 TO_DATE 없이 범위 조건을 건다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from core.codes_cache import get_codes
from core.database import get_connection

_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_SEVERITY_LABELS = {1: "낮음", 2: "보통", 3: "높음"}
_ISSUE_STATE_LABELS = {0: "미확인", 1: "정탐", 2: "오탐"}
_CCTV_STATE_LABELS = {0: "정상", 1: "점검중", 2: "고장"}
_CTYPE_LABELS = {0: "일반", 1: "휴무", 2: "이벤트", 3: "정산", 4: "점검"}


# ---------------------------------------------------------------------------
# 검토 대상 이슈 (서버가 DB에서 읽어 고정하는 값)
# ---------------------------------------------------------------------------

@dataclass
class IssueContext:
    no: int                 # CCTV_ISSUE.NO
    cno: int                # CCTV 번호
    sno: Optional[int]      # 매장 번호 (CCTV.SNO) - CCTV가 삭제됐으면 None
    code: str               # 이상행동 코드
    code_name: str          # 코드명 (CCTV_ISSUE_CODE)
    severity: str           # 심각도 라벨
    state: int              # 0 미확인 / 1 정탐 / 2 오탐
    comnet: str             # 상황 설명
    reliability: str        # Jetson 신뢰도 (문자열)
    cdate: str              # 발생 일시

    @property
    def at(self) -> Optional[datetime]:
        """발생 일시를 datetime으로. 형식이 다르면 None."""
        try:
            return datetime.strptime(self.cdate[:19], _DATE_FMT)
        except (TypeError, ValueError):
            return None

    def describe(self) -> dict:
        """프롬프트에 넣을 이슈 요약."""
        at = self.at
        return {
            "유형": f"{self.code_name}(코드 {self.code})",
            "심각도": self.severity,
            "Jetson 신뢰도": f"{self.reliability}%" if self.reliability else "없음",
            "발생 일시": self.cdate,
            "요일": "월화수목금토일"[at.weekday()] + "요일" if at else "알 수 없음",
            "현재 처리 상태": _ISSUE_STATE_LABELS.get(self.state, str(self.state)),
            "상황 설명": self.comnet or "없음",
        }


def _query(sql: str, params: dict) -> list:
    """SELECT 실행 후 전체 행 반환. 연결은 매번 열고 닫는다(cctv/service.py와 같은 패턴)."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def _read_clob(value) -> str:
    """COMNET은 CLOB이라 oracledb가 LOB 객체로 줄 수 있다."""
    if value is not None and hasattr(value, "read"):
        value = value.read()
    return value or ""


def _severity_label(value) -> str:
    """CCTV_ISSUE_CODE.SEVERITY는 숫자(1~3), 폴백 코드표는 한글 문자열이라 둘 다 받는다."""
    try:
        return _SEVERITY_LABELS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value) if value else "알 수 없음"


def load_issue(no: int) -> IssueContext:
    """이슈 1건과 그 CCTV의 매장 번호를 읽는다. 없으면 LookupError."""
    rows = _query(
        """
        SELECT i.NO, i.CNO, i.CODE, i.STATE, i.COMNET, i.RELIABILITY, i.CDATE, c.SNO
          FROM CCTV_ISSUE i
          LEFT JOIN CCTV c ON c.NO = i.CNO
         WHERE i.NO = :ino
        """,
        {"ino": no},
    )
    if not rows:
        raise LookupError(f"CCTV 이슈를 찾을 수 없습니다. (no={no})")

    row = rows[0]
    code = str(row[2])
    entry = get_codes().get(code, {})

    return IssueContext(
        no=int(row[0]),
        cno=int(row[1]),
        sno=int(row[7]) if row[7] is not None else None,
        code=code,
        code_name=entry.get("codeName") or code,
        severity=_severity_label(entry.get("severity")),
        state=int(row[3]) if row[3] is not None else 0,
        comnet=_read_clob(row[4]),
        reliability=str(row[5]).strip().rstrip("%") if row[5] is not None else "",
        cdate=str(row[6]) if row[6] is not None else "",
    )


def _clamp(value, default: int, low: int, high: int) -> int:
    """LLM이 준 숫자 인자를 허용 범위로 자른다(문자열·None·엉뚱한 값이 와도 안전)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _no_time() -> dict:
    return {"summary": "발생 일시 형식을 해석할 수 없어 조회하지 못했습니다.", "data": {}}


# ---------------------------------------------------------------------------
# 도구 1. 같은 CCTV의 최근 이슈
# ---------------------------------------------------------------------------

def recent_issues(ctx: IssueContext, args: dict) -> dict:
    at = ctx.at
    if at is None:
        return _no_time()

    days = _clamp(args.get("days"), default=7, low=1, high=30)
    since = (at - timedelta(days=days)).strftime(_DATE_FMT)
    burst_from = (at - timedelta(minutes=30)).strftime(_DATE_FMT)
    until = at.strftime(_DATE_FMT)

    # 이 이슈가 발생하기 "전"의 기록만 본다(나중에 다시 검토해도 같은 결과가 나오도록)
    rows = _query(
        """
        SELECT CODE, COUNT(*)
          FROM CCTV_ISSUE
         WHERE CNO = :cno AND NO <> :ino
           AND CDATE >= :since_at AND CDATE <= :until_at
         GROUP BY CODE
        """,
        {"cno": ctx.cno, "ino": ctx.no, "since_at": since, "until_at": until},
    )
    by_code = {str(code): int(count) for code, count in rows}
    total = sum(by_code.values())
    same = by_code.get(ctx.code, 0)

    burst = _query(
        """
        SELECT COUNT(*)
          FROM CCTV_ISSUE
         WHERE CNO = :cno AND NO <> :ino AND CODE = :code
           AND CDATE >= :burst_from AND CDATE <= :until_at
        """,
        {"cno": ctx.cno, "ino": ctx.no, "code": ctx.code,
         "burst_from": burst_from, "until_at": until},
    )
    burst_count = int(burst[0][0] or 0)

    codes = get_codes()
    named = {codes.get(c, {}).get("codeName", c): n for c, n in by_code.items()}

    return {
        "summary": (
            f"직전 {days}일간 같은 CCTV 이슈 {total}건 (같은 유형 {same}건), "
            f"직전 30분 내 같은 유형 {burst_count}건"
        ),
        "data": {"days": days, "total": total, "sameCode": same,
                 "sameCodeLast30Min": burst_count, "byCode": named},
    }


# ---------------------------------------------------------------------------
# 도구 2. 이 유형의 과거 정탐/오탐 이력
# ---------------------------------------------------------------------------

def false_positive_rate(ctx: IssueContext, args: dict) -> dict:
    scope = "shop" if str(args.get("scope", "cctv")).lower() == "shop" and ctx.sno else "cctv"

    if scope == "shop":
        where = "CNO IN (SELECT NO FROM CCTV WHERE SNO = :tgt)"
        target, where_label = ctx.sno, "이 매장 전체"
    else:
        where = "CNO = :tgt"
        target, where_label = ctx.cno, "이 CCTV"

    rows = _query(
        f"""
        SELECT SUM(CASE WHEN STATE = 1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN STATE = 2 THEN 1 ELSE 0 END),
               SUM(CASE WHEN STATE = 0 THEN 1 ELSE 0 END)
          FROM CCTV_ISSUE
         WHERE {where} AND CODE = :code AND NO <> :ino
        """,
        {"tgt": target, "code": ctx.code, "ino": ctx.no},
    )
    # 행이 하나도 없으면 SUM이 NULL(None)을 돌려준다
    true_cnt, false_cnt, pending = (int(v or 0) for v in rows[0])
    processed = true_cnt + false_cnt

    if processed == 0:
        summary = f"{where_label}에서 '{ctx.code_name}'을 사람이 정탐/오탐 처리한 이력이 없음 (미확인 {pending}건)"
        rate = None
    else:
        rate = round(false_cnt * 100 / processed)
        summary = (
            f"{where_label}의 '{ctx.code_name}' 처리 이력: 정탐 {true_cnt}건, 오탐 {false_cnt}건 "
            f"(오탐률 {rate}%), 미확인 {pending}건"
        )
        if processed < 5:
            summary += " - 표본이 적어 참고만"

    return {
        "summary": summary,
        "data": {"scope": scope, "truePositive": true_cnt, "falsePositive": false_cnt,
                 "unconfirmed": pending, "falseRatePercent": rate},
    }


# ---------------------------------------------------------------------------
# 도구 3. 발생 시각 전후의 방문객
# ---------------------------------------------------------------------------

def visitors_around(ctx: IssueContext, args: dict) -> dict:
    at = ctx.at
    if at is None:
        return _no_time()

    minutes = _clamp(args.get("minutes"), default=10, low=1, high=60)
    moment = at.strftime(_DATE_FMT)
    win_from = (at - timedelta(minutes=minutes)).strftime(_DATE_FMT)
    win_to = (at + timedelta(minutes=minutes)).strftime(_DATE_FMT)
    # 퇴장 처리가 안 된 채 남은 오래된 행(OUTTIME NULL)을 "지금 있는 사람"으로 세지 않도록 12시간으로 제한
    stale_from = (at - timedelta(hours=12)).strftime(_DATE_FMT)
    day = at.strftime("%Y-%m-%d")

    if ctx.sno is not None:
        cams, target = "CNO IN (SELECT NO FROM CCTV WHERE SNO = :tgt)", ctx.sno
    else:
        cams, target = "CNO = :tgt", ctx.cno

    rows = _query(
        f"""
        SELECT SUM(CASE WHEN INTIME <= :moment AND INTIME >= :stale_from
                         AND (OUTTIME IS NULL OR OUTTIME >= :moment) THEN 1 ELSE 0 END),
               SUM(CASE WHEN INTIME >= :win_from AND INTIME <= :win_to THEN 1 ELSE 0 END),
               COUNT(*)
          FROM CCTV_VISITOR
         WHERE {cams} AND SUBSTR(INTIME, 1, 10) = :ymd
        """,
        {"tgt": target, "moment": moment, "stale_from": stale_from,
         "win_from": win_from, "win_to": win_to, "ymd": day},
    )
    present, entered, day_total = (int(v or 0) for v in rows[0])

    if day_total == 0:
        # 0명과 "집계가 안 돌았다"는 다르다. LLM이 '사람이 없었다'고 단정하지 않게 구분해서 알려준다.
        summary = "당일 방문객 기록이 0건 - 실제로 손님이 없었거나 방문객 집계가 동작하지 않았을 수 있음"
    else:
        summary = (
            f"발생 시각에 매장 안에 있던 방문객 {present}명, "
            f"전후 {minutes}분 사이 입장 {entered}명 (당일 누적 {day_total}명)"
        )

    return {
        "summary": summary,
        "data": {"minutes": minutes, "presentAtMoment": present,
                 "enteredInWindow": entered, "dayTotal": day_total},
    }


# ---------------------------------------------------------------------------
# 도구 4. 당일 매장 일정 (휴무/점검 등)
# ---------------------------------------------------------------------------

def shop_calendar(ctx: IssueContext, args: dict) -> dict:
    at = ctx.at
    if at is None:
        return _no_time()
    if ctx.sno is None:
        return {"summary": "CCTV에 연결된 매장이 없어 일정을 조회하지 못했습니다.", "data": {}}

    day = at.strftime("%Y-%m-%d")
    # 시작일 <= 발생일 <= 종료일 인 일정. 날짜 부분(앞 10자)만 비교해서 종일 일정도 잡는다.
    rows = _query(
        """
        SELECT CTYPE, TITLE, SDATE, EDATE, ALLDAY
          FROM SHOP_CALENDAR
         WHERE SNO = :sno
           AND (STATUS IS NULL OR STATUS <> 'N')
           AND SUBSTR(SDATE, 1, 10) <= :ymd
           AND SUBSTR(EDATE, 1, 10) >= :ymd
         ORDER BY SDATE
        """,
        {"sno": ctx.sno, "ymd": day},
    )

    events = [
        {
            "type": _CTYPE_LABELS.get(int(r[0]) if r[0] is not None else 0, "일반"),
            "title": str(r[1] or ""),
            "start": str(r[2] or ""),
            "end": str(r[3] or ""),
            "allDay": str(r[4] or "Y") == "Y",
        }
        for r in rows[:10]
    ]

    if not events:
        summary = "당일 등록된 매장 일정 없음"
    else:
        parts = [
            f"[{e['type']}] {e['title']}" + (" (종일)" if e["allDay"] else f" ({e['start'][11:16]}~{e['end'][11:16]})")
            for e in events[:3]
        ]
        summary = f"당일 매장 일정 {len(events)}건: " + ", ".join(parts)

    return {"summary": summary, "data": {"events": events}}


# ---------------------------------------------------------------------------
# 도구 5. CCTV 장비 상태
# ---------------------------------------------------------------------------

def cctv_status(ctx: IssueContext, args: dict) -> dict:
    rows = _query(
        "SELECT CNAME, STATE, CKDATE FROM CCTV WHERE NO = :cno",
        {"cno": ctx.cno},
    )
    if not rows:
        return {"summary": "CCTV 정보를 찾을 수 없음 (삭제된 CCTV일 수 있음)", "data": {}}

    name, state, ckdate = rows[0]
    state = int(state) if state is not None else 0
    label = _CCTV_STATE_LABELS.get(state, str(state))

    return {
        "summary": f"CCTV '{name or ctx.cno}' 상태: {label}" + (f", 최근 점검일 {ckdate}" if ckdate else ""),
        "data": {"name": name, "state": label, "lastCheck": ckdate},
    }


# ---------------------------------------------------------------------------
# 도구 목록 (에이전트 프롬프트와 실행기가 같이 본다)
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    label: str                                        # 화면에 보여줄 이름
    description: str                                  # LLM에게 알려줄 용도
    args: str                                         # LLM에게 알려줄 인자 설명
    run: Callable[[IssueContext, dict], dict]


TOOLS: Dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool(
            "recent_issues", "같은 CCTV의 최근 이슈",
            "같은 CCTV에서 이 이슈 직전에 어떤 이슈가 얼마나 있었는지. 같은 유형이 짧은 시간에 반복됐는지 본다.",
            '{"days": 1~30 (기본 7)}',
            recent_issues,
        ),
        Tool(
            "false_positive_rate", "이 유형의 과거 정탐/오탐 이력",
            "사람이 이 유형을 정탐/오탐으로 처리한 이력과 오탐률. 이 카메라에서 자주 틀리는 유형인지 본다.",
            '{"scope": "cctv"(기본, 이 CCTV만) 또는 "shop"(매장 전체)}',
            false_positive_rate,
        ),
        Tool(
            "visitors_around", "발생 시각 전후 방문객",
            "발생 시각에 매장 안에 사람이 있었는지, 전후로 몇 명이 들어왔는지.",
            '{"minutes": 1~60 (기본 10)}',
            visitors_around,
        ),
        Tool(
            "shop_calendar", "당일 매장 일정",
            "발생한 날의 매장 일정(휴무·점검·이벤트 등). 공사·점검처럼 감지를 설명할 수 있는 일정이 있었는지 본다.",
            "{}",
            shop_calendar,
        ),
        Tool(
            "cctv_status", "CCTV 장비 상태",
            "이 CCTV가 정상/점검중/고장 중 어떤 상태인지.",
            "{}",
            cctv_status,
        ),
    ]
}

# LLM을 전혀 쓸 수 없을 때도 근거는 보여주기 위해 순서대로 돌리는 기본 조회
DEFAULT_TOOL_ORDER: List[str] = ["recent_issues", "false_positive_rate", "visitors_around"]
