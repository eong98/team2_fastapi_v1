import os

import oracledb
from dotenv import load_dotenv


load_dotenv()


def get_connection():
    """환경변수의 접속정보를 사용하여 Oracle DB 연결을 반환한다."""
    return oracledb.connect(
        user=os.getenv("ORACLE_USER"),
        password=os.getenv("ORACLE_PASSWORD"),
        dsn=(
            f"{os.getenv('ORACLE_HOST')}:"
            f"{os.getenv('ORACLE_PORT')}/"
            f"{os.getenv('ORACLE_SERVICE')}"
        ),
    )
