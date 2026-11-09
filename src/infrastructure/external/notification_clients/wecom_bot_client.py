"""
企业微信机器人通知客户端
"""
import asyncio
import re
from typing import Dict

import requests

from .base import NotificationClient

# 企业微信 markdown 的特殊字符；用于转义半可信的商品文本，防止注入链接/格式。
_MARKDOWN_SPECIALS_RE = re.compile(r"([\\`*_\[\]()~>#+\-=|{}.!])")


def _escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIALS_RE.sub(r"\\\1", str(text))


class WeComBotClient(NotificationClient):
    """企业微信机器人通知客户端"""

    channel_key = "wecom"
    display_name = "企业微信"

    def __init__(self, bot_url: str | None = None, pcurl_to_mobile: bool = True):
        super().__init__(enabled=bool(bot_url), pcurl_to_mobile=pcurl_to_mobile)
        self.bot_url = bot_url

    async def send(self, product_data: Dict, reason: str) -> None:
        if not self.is_enabled():
            raise RuntimeError("企业微信 未启用")

        message = self._build_message(product_data, reason)
        # 标题/原因/价格等来自半可信的商品数据，markdown 下需转义特殊字符，
        # 防止被注入 []() 链接结构；固定模板脚手架不转义。
        markdown_lines = [f"## {_escape_markdown(message.notification_title)}", ""]
        markdown_lines.append(f"- 价格: {_escape_markdown(message.price)}")
        markdown_lines.append(f"- 原因: {_escape_markdown(message.reason)}")
        if message.mobile_link:
            markdown_lines.append(
                f"- 手机端链接: [{_escape_markdown(message.mobile_link)}]({message.mobile_link})"
            )
        markdown_lines.append(
            f"- 电脑端链接: [{_escape_markdown(message.desktop_link)}]({message.desktop_link})"
        )
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": "\n".join(markdown_lines)},
        }
        headers = {"Content-Type": "application/json"}
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                self.bot_url,
                json=payload,
                headers=headers,
                timeout=10,
            ),
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errcode", 0) != 0:
            raise RuntimeError(result.get("errmsg", "企业微信返回未知错误"))
