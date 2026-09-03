"""
core/codes_cache.py — 이상행동유형코드(CCTV_ISSUE_CODE) 캐시.

cctv-ai-pipeline-design.md "확인/보완이 필요한 것" 2번:
  "CODE(문제유형코드) 참조 테이블이 없음 — 프론트(CctvIssue.ts)도 01~05 임시 매핑 상태.
   같은 값이 4곳에 중복(CctvIssue.ts, cctv/service.py, modules/cctv_issue.py,
   jetson_worker/codes.py). 실제 코드 테이블을 만들면 네 군데 모두 그걸 참조하도록
   교체 필요."

이제 실제 CCTV_ISSUE_CODE 테이블(team2_jpa_v1, dev.jpa.allimio.cctvissuecode)이 생겼으므로,
이 모듈이 그 테이블을 조회하는 유일한 창구가 됩니다. cctv/service.py와 동일하게
core.database.get_connection()으로 Oracle에 직접 붙습니다(Spring REST API를 거치지
않음 - 이 저장소의 기존 관례를 그대로 따름).

사용법
    from core.codes_cache import is_valid_code, get_code_label, get_code_map

    if not is_valid_code(code):
        raise ValueError(...)

    label = get_code_label(code)   # "01" -> "폭행" (매핑에 없으면 원본 코드 그대로 반환)
    codes = get_code_map()         # {"01": "폭행", "02": "기물파손", ...}

캐시/폴백 정책
    - 사용중(USE_YN='Y')인 코드만 조회하고, 5분(CACHE_TTL_SECONDS)간 캐시합니다.
      이상행동 코드는 자주 바뀌는 값이 아니라서 이 정도 지연은 문제없다고 보고 잡은 값입니다.
    - Oracle 연결이 실패하면(DB 다운, 테스트 환경 등) 캐시가 있으면 캐시를, 없으면
      _FALLBACK_CODE_MAP(01~05 기존 값)을 씁니다. cctv/router.py의 이슈 저장 흐름은
      안전 이벤트라서 코드 조회 실패로 저장 자체가 막히면 안 되기 때문입니다.
    - test_cctv_issue_agent.py처럼 DB 없이 LLM만 테스트하는 스크립트도 이 폴백 덕분에
      계속 Oracle 연결 없이 동작합니다.
"""

from __future__ import annotations

import logging
import time

from core.database import get_connection

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300

_cache: dict[str, str] = {}
_cache_loaded_at: float = 0.0

# Oracle 조회가 실패했을 때 쓰는 최후의 폴백입니다. CctvIssueCode.sql(team2_jpa_v1)의
# 시드 데이터와 반드시 동일하게 유지하세요 - 여기 값을 늘리려면 그 SQL에도 같이 추가해야
# 프론트/서버가 서로 어긋나지 않습니다.
_FALLBACK_CODE_MAP: dict[str, str] = {
    "01": "폭행",
    "02": "기물파손",
    "03": "쓰러짐/응급",
    "04": "무단침입",
    "05": "장시간체류",
}


def _fetch_from_db() -> dict[str, str]:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT CODE, CODE_NAME
            FROM CCTV_ISSUE_CODE
            WHERE USE_YN = 'Y'
            ORDER BY ORD
            """
        )
        return {code: code_name for code, code_name in cursor.fetchall()}

    finally:
        cursor.close()
        connection.close()


def get_code_map(force_refresh: bool = False) -> dict[str, str]:
    """사용중(USE_YN='Y')인 이상행동유형코드 전체 매핑({code: codeName}). TTL 안에서는 캐시 사용."""
    global _cache, _cache_loaded_at

    now = time.time()
    if not force_refresh and _cache and (now - _cache_loaded_at) < CACHE_TTL_SECONDS:
        return _cache

    try:
        _cache = _fetch_from_db()
        _cache_loaded_at = now
        return _cache

    except Exception as exc:  # noqa: BLE001 - 캐시 갱신 실패가 이슈 저장을 막으면 안 됨
        logger.warning("CCTV_ISSUE_CODE 조회 실패, 캐시/폴백 값을 사용합니다: %s", exc)
        return _cache or _FALLBACK_CODE_MAP


def is_valid_code(code: str | None) -> bool:
    """code가 CCTV_ISSUE_CODE에 등록된 값인지 확인 (modules/cctv_issue.py, cctv/router.py 사용)."""
    if not code:
        return False
    return code in get_code_map()


def get_code_label(code: str) -> str:
    """코드→코드명. 매핑에 없는 값은 원본 코드를 그대로 돌려줍니다(화면 쪽 관례와 동일)."""
    return get_code_map().get(code, code)
