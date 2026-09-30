"""
chatbot/chat_constants.py

챗봇 상담 코드값 — CHAT_LOG.SENDER/MTYPE, CHAT_SESSION.ENDFLOW/CMODE, 구분선 문구.
Spring(ChatSessionService)과 같은 값을 써야 합니다.
"""




SENDER_USER = 0


SENDER_AI = 1


SENDER_SYSTEM = 2


MTYPE_MENU_SELECT = 0


MTYPE_FREE_TEXT = 2


MTYPE_AI_ANSWER = 4


MTYPE_SYSTEM_NOTICE = 5


ENDFLOW_NEEDS_ADMIN = 4


ENDFLOW_SUMMARIZING = 5


ENDFLOW_AI_RESPONDING = 6


DIVIDER_AI_START = "여기부터 AI 상담입니다"


DIVIDER_AI_END = "여기까지가 AI 상담입니다"


CMODE_CLOSED = 2


EMPTY_ANSWER_FALLBACK = "죄송합니다. 지금은 답변을 만들지 못했습니다. 잠시 후 다시 질문해주시거나 관리자에게 문의해주세요."
