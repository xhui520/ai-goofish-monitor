"""
Telegram 通知客户端
"""
import asyncio
import html
import re
from typing import Dict

import requests

from src.infrastructure.config.settings import DEFAULT_TELEGRAM_API_BASE_URL

from .base import NotificationClient


def _is_safe_link(url: object) -> bool:
    """链接来自半可信抓取数据：仅放行 http(s) 协议，阻断 javascript: 等。"""
    return isinstance(url, str) and bool(re.match(r"^https?://", url.strip()))


class TelegramClient(NotificationClient):
    """Telegram 通知客户端"""

    channel_key = "telegram"
    display_name = "Telegram"

    def __init__(
        self,
        bot_token: str = None,
        chat_id: str = None,
        api_base_url: str = DEFAULT_TELEGRAM_API_BASE_URL,
        pcurl_to_mobile: bool = True,
    ):
        super().__init__(enabled=bool(bot_token and chat_id), pcurl_to_mobile=pcurl_to_mobile)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base_url = (
            (api_base_url or DEFAULT_TELEGRAM_API_BASE_URL).rstrip("/")
        )

    async def send(self, product_data: Dict, reason: str) -> None:
        """发送 Telegram 通知"""
        if not self.is_enabled():
            raise RuntimeError("Telegram 未启用")

        message = self._build_message(product_data, reason)
        # 标题/原因等来自半可信的商品数据，parse_mode=HTML 下必须转义，
        # 否则会被注入 <a href=...> 等标签造成钓鱼链接。
        safe_title = html.escape(message.title[:50], quote=False)
        safe_title_suffix = "..." if len(message.title) > 50 else ""
        safe_price = html.escape(message.price, quote=False)
        safe_reason = html.escape(message.reason, quote=False)
        telegram_message = [
            "🚨 <b>新推荐!</b>",
            "",
            f"<b>{safe_title}{safe_title_suffix}</b>",
            "",
            f"💰 价格: {safe_price}",
            f"📝 原因: {safe_reason}",
        ]
        # 链接同样来自半可信数据：协议校验 + 引号转义，防止 href 被注入
        if message.mobile_link and _is_safe_link(message.mobile_link):
            telegram_message.append(
                f"📱 <a href='{html.escape(message.mobile_link, quote=True)}'>手机端链接</a>"
            )
        if _is_safe_link(message.desktop_link):
            telegram_message.append(
                f"💻 <a href='{html.escape(message.desktop_link, quote=True)}'>电脑端链接</a>"
            )

        telegram_api_url = f"{self.api_base_url}/bot{self.bot_token}/sendMessage"
        telegram_payload = {
            "chat_id": self.chat_id,
            "text": "\n".join(telegram_message),
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }

        headers = {"Content-Type": "application/json"}
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                telegram_api_url,
                json=telegram_payload,
                headers=headers,
                timeout=10
            )
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram 返回未知错误"))
