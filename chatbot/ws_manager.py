# chatbot/ws_manager.py (신규)
"""
챗봇 실시간 알림을 위한 WebSocket 연결 관리자.

사용자(mno 또는 gno)별로 현재 연결된 WebSocket을 메모리에 보관합니다.
AI 답변이 저장되는 시점에, 그 세션 소유자에게 연결이 살아있으면
즉시 "새 메시지 도착" 신호를 보냅니다.
"""

from fastapi import WebSocket


class ChatWSManager:
    def __init__(self):
        # key: "mno:123" 또는 "gno:uuid문자열" / value: 연결된 WebSocket 목록(여러 탭 가능)
        self.connections: dict[str, list[WebSocket]] = {}

    def _key(self, mno: int | None, gno: str | None) -> str:
        return f"mno:{mno}" if mno else f"gno:{gno}"

    async def connect(self, websocket: WebSocket, mno: int | None, gno: str | None):
        await websocket.accept()
        key = self._key(mno, gno)
        self.connections.setdefault(key, []).append(websocket)

    def disconnect(self, websocket: WebSocket, mno: int | None, gno: str | None):
        key = self._key(mno, gno)
        if key in self.connections:
            self.connections[key] = [ws for ws in self.connections[key] if ws != websocket]
            if not self.connections[key]:
                del self.connections[key]

    async def notify(self, mno: int | None, gno: str | None, payload: dict):
        """해당 사용자에게 연결된 모든 탭에 알림을 보냅니다. 연결이 없으면 조용히 무시합니다."""
        key = self._key(mno, gno)
        dead = []
        for ws in list(self.connections.get(key, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)  # 끊긴 연결은 목록에서 제거 (계속 쌓이지 않게)
        for ws in dead:
            self.disconnect(ws, mno, gno)


ws_manager = ChatWSManager()