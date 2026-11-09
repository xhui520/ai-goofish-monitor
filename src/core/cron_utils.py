"""
Cron 解析与校验工具。
"""
from __future__ import annotations

import re
from typing import Optional

from apscheduler.triggers.cron import CronTrigger

CRON_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

CRON_FORMAT_HINT = (
    "Cron 表达式无效。支持 5 段（分 时 日 月 周）、"
    "6 段（秒 分 时 日 月 周）和常见别名（@hourly/@daily/@weekly/@monthly/@yearly）。"
    "星期字段遵循标准 crontab 约定：0/7=周日、1=周一 …… 6=周六。"
    "示例：*/15 * * * *、0 8 * * *、0 0 * * 0（每周日）、@daily。"
)

# 单个星期字段元素：数字、区间、步进（a/b、a-b、*/n），或英文名（mon-sun）
_DOW_ELEMENT_RE = re.compile(r"^(?P<start>\d+|\*)(?:-(?P<end>\d+))?(?:/(?P<step>\d+))?$")


def _std_dow_to_apscheduler(number: str) -> str:
    """标准 crontab 星期数字（0/7=周日）转 APScheduler 约定（0=周一 ... 6=周日）。"""
    value = int(number)
    return str((value - 1) % 7)


def _convert_dow_element(element: str) -> str:
    match = _DOW_ELEMENT_RE.match(element)
    if not match:
        # 英文名（mon、fri-sun 等）APScheduler 本身就按正确语义解析
        return element
    start, end, step = match.group("start"), match.group("end"), match.group("step")
    if start == "*":
        # */n 中的 n 是步进计数，不是具体星期，不转换
        return element
    converted = _std_dow_to_apscheduler(start)
    if end is not None:
        converted = f"{converted}-{_std_dow_to_apscheduler(end)}"
    if step is not None:
        converted = f"{converted}/{step}"
    return converted


def _convert_day_of_week_field(field: str) -> str:
    return ",".join(_convert_dow_element(part) for part in field.split(","))


def normalize_cron_expression(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    normalized = " ".join(str(value).strip().split())
    if not normalized:
        return None

    return CRON_ALIASES.get(normalized.lower(), normalized)


def build_cron_trigger(
    expression: str,
    *,
    timezone=None,
) -> CronTrigger:
    normalized = normalize_cron_expression(expression)
    if normalized is None:
        raise ValueError(CRON_FORMAT_HINT)

    parts = normalized.split()
    try:
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
            return CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                # APScheduler 的 day_of_week 是 0=周一；用户输入按标准
                # crontab（0/7=周日），这里做一次语义转换
                day_of_week=_convert_day_of_week_field(day_of_week),
                timezone=timezone,
            )

        if len(parts) == 6:
            second, minute, hour, day, month, day_of_week = parts
            return CronTrigger(
                second=second,
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=_convert_day_of_week_field(day_of_week),
                timezone=timezone,
            )
    except ValueError as exc:
        raise ValueError(CRON_FORMAT_HINT) from exc

    raise ValueError(CRON_FORMAT_HINT)


def validate_cron_expression(value: Optional[str]) -> Optional[str]:
    normalized = normalize_cron_expression(value)
    if normalized is None:
        return None

    build_cron_trigger(normalized)
    return normalized
