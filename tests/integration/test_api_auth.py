"""
认证与鉴权集成测试

覆盖：未登录 401、登录签发会话、伪造令牌拒绝、Bearer 支持、登出、
登录失败限速、公开端点（/health）无需认证。
"""
import pytest
from fastapi.testclient import TestClient

from src.api import auth as auth_module
from src.app import app
from src.infrastructure.config.settings import settings as app_settings


@pytest.fixture()
def auth_env(monkeypatch):
    """固定测试账号密码并清空限速状态，避免本机 .env 与限速缓存干扰。"""
    monkeypatch.setattr(app_settings, "web_username", "tester")
    monkeypatch.setattr(app_settings, "web_password", "secret-pass")
    auth_module._login_failures.clear()
    return {"username": "tester", "password": "secret-pass"}


@pytest.fixture()
def client(auth_env):
    return TestClient(app)


def _login(client, auth_env, password=None):
    return client.post(
        "/auth/status",
        json={
            "username": auth_env["username"],
            "password": password if password is not None else auth_env["password"],
        },
    )


def test_unauthenticated_api_returns_401(client):
    response = client.get("/api/tasks")
    assert response.status_code == 401


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_login_wrong_password_returns_401(client, auth_env):
    response = _login(client, auth_env, password="wrong")
    assert response.status_code == 401


def test_login_success_sets_cookie_and_token(client, auth_env):
    response = _login(client, auth_env)
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["token"]
    assert auth_module.verify_session_token(body["token"])
    # HttpOnly Cookie 已随响应设置
    assert "auth_token" in response.cookies


def test_authenticated_request_after_login(client, auth_env):
    _login(client, auth_env)
    response = client.get("/api/tasks")
    assert response.status_code == 200


def test_bearer_token_accepted(client, auth_env):
    token = _login(client, auth_env).json()["token"]
    response = client.get(
        "/api/tasks", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_forged_token_rejected(client):
    response = client.get(
        "/api/tasks", cookies={"auth_token": "9999999999:deadbeef"}
    )
    assert response.status_code == 401


def test_logout_clears_session(client, auth_env):
    _login(client, auth_env)
    assert client.post("/auth/logout").status_code == 200
    response = client.get("/api/tasks")
    assert response.status_code == 401


def test_rate_limit_after_repeated_failures(client, auth_env):
    for _ in range(auth_module.LOGIN_FAILURE_LIMIT):
        _login(client, auth_env, password="bad")
    # 达到失败上限后，即使密码正确也返回 429
    response = _login(client, auth_env)
    assert response.status_code == 429


def test_verify_session_token_rejects_garbage():
    assert auth_module.verify_session_token(None) is False
    assert auth_module.verify_session_token("") is False
    assert auth_module.verify_session_token("no-signature") is False
    assert auth_module.verify_session_token("abc:def") is False
    token = auth_module.create_session_token()
    assert auth_module.verify_session_token(token) is True
    # 篡改过期时间戳后签名不再匹配
    expires, _, signature = token.rpartition(":")
    assert auth_module.verify_session_token(f"{int(expires) + 60}:{signature}") is False
