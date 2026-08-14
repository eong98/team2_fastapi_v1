import socket

from dotenv import load_dotenv
from langchain_ollama import ChatOllama


load_dotenv(
    dotenv_path="./.env",
    override=True
)


H200_IP = "139.150.91.194"


def is_h200() -> bool:
    """
    현재 실행 환경이 H200 서버인지 확인한다.
    """
    ip_address = socket.gethostbyname(
        socket.gethostname()
    )

    return ip_address == H200_IP


def get_llm():
    """
    실행 환경에 따라 사용할 Ollama 모델을 구분한다.

    H200 : gemma4:26b
    Local: gemma2:9b
    """

    if is_h200():
        print("-> H200 GPU 사용")

        return ChatOllama(
            model="gemma4:26b",
            temperature=0,
            format="json"
        )

    print("-> Local CPU 사용")

    return ChatOllama(
        model="gemma2:9b",
        temperature=0,
        format="json"
    )