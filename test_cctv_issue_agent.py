"""
modules/cctv_issue.py (comnet 문장 다듬기 모듈) 단독 테스트 스크립트.

Jetson, FastAPI 서버 실행, Oracle DB 연결 전부 필요 없습니다.
로컬(or 서버)에 Ollama만 떠 있으면 이 파일 하나로 확인 가능합니다.

⚠️ 이 모듈은 더 이상 유형(code)을 재분류하지 않습니다 - Jetson이 이미 확정한
   code를 검증만 하고, detail을 관리자용 문장(comnet)으로 다듬는 역할만 합니다.
   그래서 아래 테스트 케이스도 "code를 이미 알고 있는 상태"로 넣습니다
   (예전처럼 candidate_type만 주고 code를 맞혀보라고 시키지 않습니다).

사용법:
    1. team2_fastapi_v1 저장소를 로컬에 clone 하고, cctv/, modules/cctv_issue.py를
       (zip대로) 저장소 루트에 복사해 넣습니다.
    2. 이 파일을 저장소 루트(main.py와 같은 위치)에 둡니다.
    3. Ollama가 로컬에서 돌고 있는지 확인:
         ollama list
       core/llm_client.py는 실행 환경 IP가 H200이 아니면 자동으로
       "gemma2:9b"를 씁니다. 없으면:
         ollama pull gemma2:9b
    4. 실행:
         python test_cctv_issue_agent.py

각 케이스마다 comnet 문장이 자연스럽게 나오는지, code/reliability는 입력값이
그대로 유지되는지(=재분류하지 않는지)를 확인하면 됩니다.
"""

from core.codes_cache import get_code_map
from modules.cctv_issue import analyze_cctv_issue


TEST_CASES = [
    {
        "label": "폭행 (Jetson이 이미 01로 확정)",
        "code": "01",
        "detail": "두 사람의 바운딩박스 중심 거리가 42px까지 좁혀지고 0.3초간 유지됨",
        "confidence": 0.8,
    },
    {
        "label": "쓰러짐 (Jetson이 이미 03으로 확정)",
        "code": "03",
        "detail": "한 사람의 바운딩박스가 가로로 길게 눕는 형태로 30프레임 이상 유지됨",
        "confidence": 0.5,
    },
    {
        "label": "무단침입 (Jetson이 이미 04로 확정)",
        "code": "04",
        "detail": "영업시간(8시~23시) 외 새벽 2시에 매장 CCTV에서 사람이 감지됨",
        "confidence": 0.7,
    },
    {
        "label": "잘못된 code (서버가 400으로 막아야 하는 케이스)",
        "code": "99",
        "detail": "존재하지 않는 코드값 테스트",
        "confidence": 0.5,
    },
]


def main():
    code_map = get_code_map()
    print(f"CODE_MAP: {code_map}\n")

    for case in TEST_CASES:
        print("=" * 70)
        print(f"[{case['label']}]")
        print(f"  code={case['code']!r}")
        print(f"  detail={case['detail']!r}")
        print(f"  confidence={case['confidence']}")

        try:
            result = analyze_cctv_issue(
                code=case["code"],
                detail=case["detail"],
                confidence=case["confidence"],
            )

            code_label = code_map.get(result["code"], "?")

            print(
                f"  -> code={result['code']}({code_label}), "
                f"reliability={result['reliability']}, "
                f"comnet={result['comnet']!r}"
            )

            assert result["code"] == case["code"], "code가 재분류되면 안 됨!"
            assert result["reliability"] == round(case["confidence"] * 100), (
                "reliability는 confidence 그대로여야 함!"
            )

        except Exception as e:
            print(f"  -> 에러(의도된 케이스일 수 있음): {e}")

    print("=" * 70)


if __name__ == "__main__":
    main()