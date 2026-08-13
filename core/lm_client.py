from langchain_ollama import ChatOllama


MODEL_NAME = "gemma4:26b"


def get_llm():
    """
    Allimio 공용 LLM 클라이언트.

    모든 AI 모듈에서 동일한 로컬 Ollama 모델
    gemma4:26b를 사용한다.
    """
    return ChatOllama(
        model=MODEL_NAME,
        temperature=0,
        format="json"
    )