"""
通知服务
统一管理所有通知渠道
"""
import asyncio
import re
from typing import Dict, List

from src.infrastructure.external.notification_clients.base import NotificationClient
from src.infrastructure.external.notification_clients.factory import build_notification_clients
from src.services.notification_config_service import load_notification_settings
from src.infrastructure.config.settings import NotificationSettings

# raise_for_status 的异常文本包含完整请求 URL，可能携带
# Telegram bot token、Bark key、企业微信 webhook key 等凭据。
# 回传给前端的错误消息必须先脱敏。
_SENSITIVE_URL_PARTS = re.compile(
    r"""
    (?:
        bot\d[^/\s&'"]+                      # Telegram: /bot<token>/method
      | [?&](?:key|token|secret)=[^\s&'"]+   # 查询串中的 key/token/secret
      | /webhook/[^\s&'"]+                   # 企业微信等 webhook/<key>
      | (?<=/)[A-Za-z0-9_.:-]{20,}(?=[/?]|$) # 路径中的长随机凭据段（如 Bark key）
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def sanitize_error_message(exc: Exception) -> str:
    """生成可安全展示给前端的错误消息（去除 URL 中的凭据段）。"""
    return _SENSITIVE_URL_PARTS.sub("<REDACTED>", str(exc))


class NotificationService:
    """通知服务"""

    def __init__(self, clients: List[NotificationClient]):
        self.clients = [client for client in clients if client.is_enabled()]

    async def send_notification(
        self,
        product_data: Dict,
        reason: str,
    ) -> Dict[str, Dict[str, str | bool]]:
        """
        发送通知到所有启用的渠道

        Returns:
            各渠道发送结果，包含成功状态和消息
        """
        if not self.clients:
            return {}

        tasks = [
            self._send_with_result(client, product_data, reason)
            for client in self.clients
        ]
        results = await asyncio.gather(*tasks)
        return {result["channel"]: result for result in results}

    async def send_test_notification(self) -> Dict[str, Dict[str, str | bool]]:
        test_product = {
            "商品标题": "[测试通知] 闲鱼智能监控",
            "当前售价": "0",
            "商品链接": "https://www.goofish.com/",
        }
        return await self.send_notification(
            test_product,
            "这是一条测试通知，用于验证推送渠道是否可用。",
        )

    async def _send_with_result(
        self,
        client: NotificationClient,
        product_data: Dict,
        reason: str,
    ) -> Dict[str, str | bool]:
        try:
            await client.send(product_data, reason)
            return {
                "channel": client.channel_key,
                "label": client.display_name,
                "success": True,
                "message": "发送成功",
            }
        except Exception as exc:
            return {
                "channel": client.channel_key,
                "label": client.display_name,
                "success": False,
                "message": sanitize_error_message(exc),
            }


def build_notification_service(
    settings: NotificationSettings | None = None,
) -> NotificationService:
    notification_settings = settings or load_notification_settings()
    return NotificationService(build_notification_clients(notification_settings))
