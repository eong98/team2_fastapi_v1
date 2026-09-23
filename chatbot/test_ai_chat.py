"""
chatbot/test_ai_chat.py

process_ai_chat() 통합 테스트 — 실제로 CHAT_LOG에 저장까지 되는지 확인합니다.

사용법:
    python -m chatbot.test_ai_chat "c71e7af2-1e05-497f-bdfb-357da81ce567"
"""

import sys

from chatbot.service import process_ai_chat


def main():
    if len(sys.argv) < 2:
        print("사용법: python -m chatbot.test_ai_chat <세션번호(SNO)>")
        sys.exit(1)

    sno = sys.argv[1]

    test_messages = [
        "구독권 취소하려면 어떻게 해야 하나요?",
        "CCTV 화면이 안 나와요",
        "내일 만료일인데 취소 가능한가요?",
    ]

    for msg in test_messages:
        print("=" * 70)
        print(f"[질문] {msg}")
        try:
            result = process_ai_chat(sno, msg)
            for log in result["logs"]:
                sender_label = {0: "사용자", 1: "AI", 2: "상담봇"}[log["sender"]]
                print(f"  [{sender_label}] (NO={log['no']}) {log['content'][:80]}")
            print(f"  needsAdmin={result['needsAdmin']}")
        except Exception as e:
            print(f"  실패: {e}")

    print("=" * 70)
    print("Oracle에서 직접 확인:")
    print(f"  SELECT * FROM CHAT_LOG WHERE SNO = '{sno}' ORDER BY NO;")
    print(f"  SELECT ENDFLOW FROM CHAT_SESSION WHERE NO = '{sno}';")


if __name__ == "__main__":
    main()