"""
WebSocket 路由集成测试

覆盖：无凭证连接被拒绝、携带合法令牌可连接、广播对失效连接的
超时剔除（僵死连接不阻塞广播）。
"""
import asyncio

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api import auth as auth_module
from src.api.routes import websocket


@pytest.fixture()
def ws_app(monkeypatch):
    # 缩短广播超时，避免测试等待真实 5 秒
    monkeypatch.setattr(websocket, "_BROADCAST_TIMEOUT_SECONDS", 0.2)

    app = FastAPI()
    app.include_router(
        websocket.router, dependencies=[Depends(auth_module.verify_ws_auth)]
    )
    return app


def _connect_with_token(client: TestClient):
    token = auth_module.create_session_token()
    return client.websocket_connect(f"/ws?token={token}")


def test_websocket_without_credentials_rejected(ws_app):
    client = TestClient(ws_app)
    with pytest.raises(Exception):
        # 无 cookie、无 token：应被鉴权依赖以 1008 拒绝
        with client.websocket_connect("/ws"):
            pass


def test_websocket_with_valid_token_accepted(ws_app):
    client = TestClient(ws_app)
    with _connect_with_token(client) as ws:
        # 连接建立即视为通过鉴权（服务端会在 accept 后挂起等待消息）
        assert ws is not None


def test_broadcast_skips_dead_connection(ws_app):
    """发送抛异常的连接应被剔除，其余连接仍收到消息。"""

    class FakeWebSocket:
        def __init__(self, *, broken: bool, slow: bool = False):
            self.broken = broken
            self.slow = slow
            self.received = []

        async def send_json(self, message):
            if self.slow:
                await asyncio.sleep(10)
            if self.broken:
                raise RuntimeError("connection closed")
            self.received.append(message)

    async def scenario():
        alive = FakeWebSocket(broken=False)
        broken = FakeWebSocket(broken=True)
        slow = FakeWebSocket(broken=False, slow=True)
        websocket.active_connections.update({alive, broken, slow})
        try:
            await asyncio.wait_for(
                websocket.broadcast_message("task_status_changed", {"id": 1}),
                timeout=3,
            )
            remaining = set(websocket.active_connections)
        finally:
            websocket.active_connections.clear()
        return alive, broken, slow, remaining

    alive, broken, slow, remaining = asyncio.run(scenario())
    assert alive.received, "存活连接应收到广播"
    assert alive.received[0]["type"] == "task_status_changed"
    # 发送失败与超时（僵死）的连接都应被剔除
    assert remaining == {alive}
