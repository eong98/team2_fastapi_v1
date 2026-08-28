import json

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_client import get_llm


llm = get_llm()


def translate_notification(title: str, content: str, lang: str) -> tuple[str | None, str | None]:
    """알림 제목/내용 번역. 실패하면 NULL용 None 반환."""

    if not lang or lang.lower() in {"ko", "ko-kr"}:
        return None, None

    prompt = f"""
다음 알림을 {lang} 언어로 자연스럽게 번역하세요.
의미를 추가하거나 삭제하지 마세요.

제목: {title}
내용: {content}

JSON만 반환하세요.
{{"title":"...","content":"..."}}
"""

    try:
        response = llm.invoke([
            SystemMessage(content="알림 번역 AI입니다. 반드시 JSON만 반환하세요."),
            HumanMessage(content=prompt),
        ])

        text = response.content.strip()

        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]

        if text.endswith("```"):
            text = text[:-3]

        result = json.loads(text.strip())

        translated_title = str(result.get("title", "")).strip() or None
        translated_content = str(result.get("content", "")).strip() or None

        return translated_title, translated_content

    except Exception:
        return None, None
