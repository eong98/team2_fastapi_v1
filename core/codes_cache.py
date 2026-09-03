# -*- coding: utf-8 -*-
"""
core/codes_cache.py

Oracle CCTV_ISSUE_CODE 테이블(코드/코드명/설명/심각도/사용여부)을 조회해서
5분간 메모리에 캐싱한다. DB 조회가 실패하면(연결 끊김 등) 01~05 기본값으로 폴백한다
(안전 이벤트라 코드 테이블 문제로 이슈 저장 자체가 막히면 안 되기 때문).

CCTV_ISSUE_CODE 컬럼: CODE(PK) / CODE_NAME / DESCRIPTION / SEVERITY / ORD / USE_YN / CDATE
"""

import time

from core.database import get_connection

_CACHE_TTL_SEC = 300  # 5분

_cache: dict = {}
_cache_loaded_at: float = 0.0

# DB 조회 실패 시에만 쓰는 폴백값 (관리자 화면 "이상행동유형코드 이벤트 관리"의 시드 데이터와 동일)
_FALLBACK_CODES = {
    "01": {"codeName": "폭행", "severity": "높음", "useYn": "Y"},
    "02": {"codeName": "기물파손", "severity": "보통", "useYn": "Y"},
    "03": {"codeName": "쓰러짐/응급", "severity": "높음", "useYn": "Y"},
    "04": {"codeName": "무단침입", "severity": "보통", "useYn": "Y"},
    "05": {"codeName": "장시간체류", "severity": "낮음", "useYn": "Y"},
}


def _load_from_db() -> dict:
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT CODE, CODE_NAME, SEVERITY, USE_YN
            FROM CCTV_ISSUE_CODE
            """
        )
        rows = cursor.fetchall()
        return {
            row[0]: {"codeName": row[1], "severity": row[2], "useYn": row[3]}
            for row in rows
        }
    finally:
        cursor.close()
        connection.close()


def get_codes(force_refresh: bool = False) -> dict:
    """{code: {codeName, severity, useYn}} 형태로 반환. 캐시가 오래됐으면 DB에서 다시 읽는다."""
    global _cache, _cache_loaded_at

    now = time.time()
    if force_refresh or not _cache or (now - _cache_loaded_at) > _CACHE_TTL_SEC:
        try:
            _cache = _load_from_db()
            _cache_loaded_at = now
        except Exception as e:
            print(f"[codes_cache] CCTV_ISSUE_CODE 조회 실패, 폴백 사용: {e}")
            if not _cache:
                _cache = dict(_FALLBACK_CODES)
            # 실패했을 때는 _cache_loaded_at을 갱신하지 않음 -> 다음 호출에서 다시 재시도

    return _cache


def is_valid_code(code: str) -> bool:
    codes = get_codes()
    entry = codes.get(code)
    return entry is not None and entry.get("useYn", "Y") == "Y"
