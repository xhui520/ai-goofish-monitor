"""
认证与鉴权支持

为 Web API 提供基于 HMAC 签名会话令牌的统一鉴权：
- 登录成功后签发令牌，通过 HttpOnly Cookie 或 ``Authorization: Bearer`` 传递
- 密码校验使用常数时间比较，并带有基于 IP 的登录失败限速
- 令牌密钥在每次进程重启时随机生成，重启后所有会话失效（用户需重新登录）
"""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request, WebSocket, WebSocketException, status

from src.infrastructure.config.settings import settings as app_settings

SESSION_COOKIE_NAME = "auth_token"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 天

# 登录失败限速：同一 IP 在窗口期内最多失败 N 次
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 300

# 每次进程启动时随机生成，保证令牌不可伪造且重启即失效
_SECRET_KEY = secrets.token_bytes(32)

_login_failures: Dict[str, Deque[float]] = defaultdict(deque)


def create_session_token() -> str:
    """签发一个带过期时间的 HMAC 签名会话令牌。"""
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    signature = hmac.new(
        _SECRET_KEY, str(expires_at).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{expires_at}:{signature}"


def verify_session_token(token: Optional[str]) -> bool:
    """校验会话令牌的签名与有效期（常数时间比较）。"""
    if not token or ":" not in token:
        return False
    expires_part, _, signature = token.rpartition(":")
    if not expires_part.isdigit():
        return False
    expected = hmac.new(
        _SECRET_KEY, expires_part.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return time.time() < int(expires_part)


def verify_credentials(username: str, password: str) -> bool:
    """常数时间比较用户名与密码，避免时序侧信道。"""
    username_ok = hmac.compare_digest(
        username.encode("utf-8"), app_settings.web_username.encode("utf-8")
    )
    password_ok = hmac.compare_digest(
        password.encode("utf-8"), app_settings.web_password.encode("utf-8")
    )
    return username_ok and password_ok


def _prune_login_failures(client_ip: str, now: float) -> None:
    window = _login_failures.get(client_ip)
    if not window:
        return
    cutoff = now - LOGIN_FAILURE_WINDOW_SECONDS
    while window and window[0] < cutoff:
        window.popleft()


def is_login_rate_limited(client_ip: str) -> bool:
    """判断该 IP 近期登录失败次数是否已达上限。"""
    _prune_login_failures(client_ip, time.time())
    return len(_login_failures.get(client_ip, ())) >= LOGIN_FAILURE_LIMIT


def register_login_failure(client_ip: str) -> None:
    now = time.time()
    _prune_login_failures(client_ip, now)
    _login_failures[client_ip].append(now)


def clear_login_failures(client_ip: str) -> None:
    _login_failures.pop(client_ip, None)


def _extract_bearer_token(auth_header: Optional[str]) -> Optional[str]:
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None


async def verify_auth(request: Request) -> None:
    """HTTP API 统一鉴权依赖：校验 Cookie 或 Bearer 令牌。"""
    token = _extract_bearer_token(request.headers.get("authorization")) or \
        request.cookies.get(SESSION_COOKIE_NAME)
    if verify_session_token(token):
        return
    raise HTTPException(status_code=401, detail="未登录或会话已过期")


async def verify_ws_auth(websocket: WebSocket) -> None:
    """WebSocket 鉴权依赖：浏览器自动携带 Cookie，非浏览器客户端可用 ?token= 或 Bearer。"""
    token = (
        websocket.cookies.get(SESSION_COOKIE_NAME)
        or websocket.query_params.get("token")
        or _extract_bearer_token(websocket.headers.get("authorization"))
    )
    if verify_session_token(token):
        return
    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
