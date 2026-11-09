"""Task-level failure circuit breaker.

目标:
- 当登录态失效/风控导致任务持续失败时，避免无限重试、避免高频请求。
- 失败达到阈值后暂停任务一段时间。
- 暂停期间最多每天通知一次，直到用户更新 cookies / 登录态文件后自动恢复。

说明:
- 仅使用标准库，既可被 API 主进程使用，也可被爬虫子进程使用。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


try:
    from zoneinfo import ZoneInfo  # py3.9+

    def _load_tz(name: str):
        return ZoneInfo(name)


except Exception:  # pragma: no cover

    def _load_tz(name: str):
        return None


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now(tz_name: str, now: Optional[datetime] = None) -> datetime:
    if now is not None:
        return now
    tz = _load_tz(tz_name)
    if tz is None:
        return datetime.now()
    return datetime.now(tz)


def _today_str(tz_name: str, now: Optional[datetime] = None) -> str:
    return _now(tz_name, now=now).date().isoformat()


def _dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_aligned(dt: Optional[datetime], tz) -> Optional[datetime]:
    """把 aware/naive 时间统一到同一时钟后再比较，避免混用抛 TypeError。

    - 有时区库：naive 视为 tz 本地时间；aware 统一换算到 tz。
    - 无时区库：naive 原样保留（视为同一本地时钟）；aware 先换算成 UTC 再去掉时区。
    """
    if dt is None:
        return None
    if tz is not None:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _get_mtime(path: Optional[str]) -> Optional[float]:
    if not path:
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _cookie_changed(
    cookie_path: Optional[str], previous_mtime: Optional[float]
) -> bool:
    if not cookie_path:
        return False
    current = _get_mtime(cookie_path)
    if current is None or previous_mtime is None:
        return False
    return current > (previous_mtime + 1e-6)


class _FileLock:
    """跨平台的排他文件锁：POSIX 用 fcntl.flock，Windows 用 msvcrt.locking。

    msvcrt.locking(LK_LOCK) 在被占用时会重试约 10 秒后抛 OSError，
    因此 Windows 上真正拿不到锁时以异常暴露，避免静默并发写坏状态文件。
    """

    def __init__(self, fh):
        self._fh = fh
        self._locked = False

    def __enter__(self):
        if os.name == "nt":
            import msvcrt

            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            self._locked = True
        else:
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                self._locked = True
            except Exception:
                # POSIX 上 flock 失败（如特殊文件系统）时保持旧行为继续执行。
                pass
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._locked:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        return False


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _read_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        # 文件损坏时保留现场，避免无限解析失败。
        try:
            ts = str(int(time.time()))
            os.replace(path, f"{path}.corrupt.{ts}")
        except Exception:
            pass
        return {}


def _atomic_write_json(path: str, data: dict) -> None:
    _ensure_parent_dir(path)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass(frozen=True)
class SkipDecision:
    skip: bool
    should_notify: bool
    reason: str
    paused_until: Optional[datetime]
    consecutive_failures: int


class FailureGuard:
    def __init__(
        self,
        path: Optional[str] = None,
        *,
        threshold: Optional[int] = None,
        pause_seconds: Optional[int] = None,
        tz_name: Optional[str] = None,
    ):
        self.path = (
            path
            or os.getenv("TASK_FAILURE_GUARD_PATH")
            or "logs/task-failure-guard.json"
        )
        self.threshold = max(
            1, threshold or _as_int(os.getenv("TASK_FAILURE_THRESHOLD"), 3)
        )
        self.pause_seconds = max(
            60,
            pause_seconds
            or _as_int(os.getenv("TASK_FAILURE_PAUSE_SECONDS"), 24 * 60 * 60),
        )
        self.tz_name = tz_name or os.getenv("TASK_FAILURE_TZ") or "Asia/Shanghai"

    def _load(self) -> dict:
        data = _read_json_file(self.path)
        if "tasks" not in data or not isinstance(data.get("tasks"), dict):
            data = {"version": 1, "tasks": {}}
        data.setdefault("version", 1)
        return data

    def _save(self, data: dict) -> None:
        _atomic_write_json(self.path, data)

    def _update_task(self, task_key: str, updater) -> dict:
        _ensure_parent_dir(self.path)
        # 独立锁文件：Windows 下 os.replace 无法替换仍被其他句柄打开的目标文件，
        # 因此把锁加在 <path>.lock 上，数据文件本身用“临时文件+原子替换”写回。
        with open(f"{self.path}.lock", "a+", encoding="utf-8") as lock_fh:
            with _FileLock(lock_fh):
                data = self._load()
                tasks = data.setdefault("tasks", {})
                entry = tasks.get(task_key) or {}
                if not isinstance(entry, dict):
                    entry = {}
                entry = updater(entry) or entry
                tasks[task_key] = entry
                self._save(data)
                return entry

    def record_success(self, task_key: str, *, now: Optional[datetime] = None) -> None:
        def _reset(_: dict) -> dict:
            current = _now(self.tz_name, now=now)
            return {
                "consecutive_failures": 0,
                "paused_until": None,
                "last_notified_date": None,
                "last_failure_reason": None,
                "last_failure_at": None,
                "last_success_at": _dt_to_str(current),
                "cookie_path": None,
                "cookie_mtime": None,
            }

        self._update_task(task_key, _reset)

    def should_skip_start(
        self,
        task_key: str,
        *,
        cookie_path: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> SkipDecision:
        current = _now(self.tz_name, now=now)
        today = _today_str(self.tz_name, now=current)

        data = self._load()
        entry = (data.get("tasks") or {}).get(task_key) or {}
        if not isinstance(entry, dict):
            entry = {}

        paused_until = _str_to_dt(entry.get("paused_until"))
        consecutive = _as_int(entry.get("consecutive_failures"), 0)
        last_reason = (entry.get("last_failure_reason") or "").strip() or "未知错误"
        last_notified_date = entry.get("last_notified_date")

        # 存储的 ISO 字符串可能带时区（Docker 写入）而当前时间为 naive（无 tzdata），
        # 先统一时钟再比较，避免 aware/naive 混用抛 TypeError。
        tz = _load_tz(self.tz_name)
        paused_cmp = _to_aligned(paused_until, tz)
        current_cmp = _to_aligned(current, tz)

        previous_cookie_mtime = entry.get("cookie_mtime")
        if cookie_path and previous_cookie_mtime is not None:
            try:
                previous_cookie_mtime = float(previous_cookie_mtime)
            except (TypeError, ValueError):
                previous_cookie_mtime = None

        if (
            paused_until
            and paused_cmp > current_cmp
            and cookie_path
            and _cookie_changed(cookie_path, previous_cookie_mtime)
        ):
            # cookies / 登录态更新 => 自动恢复
            self.record_success(task_key, now=current)
            return SkipDecision(
                skip=False,
                should_notify=False,
                reason="cookie_updated",
                paused_until=None,
                consecutive_failures=0,
            )

        if paused_until and current_cmp < paused_cmp:
            should_notify = last_notified_date != today

            if should_notify:

                def _touch(e: dict) -> dict:
                    e = dict(e or {})
                    e["last_notified_date"] = today
                    return e

                self._update_task(task_key, _touch)

            return SkipDecision(
                skip=True,
                should_notify=should_notify,
                reason=last_reason,
                paused_until=paused_until,
                consecutive_failures=consecutive,
            )

        return SkipDecision(
            skip=False,
            should_notify=False,
            reason="not_paused",
            paused_until=None,
            consecutive_failures=consecutive,
        )

    def record_failure(
        self,
        task_key: str,
        reason: str,
        *,
        cookie_path: Optional[str] = None,
        min_failures_to_pause: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        current = _now(self.tz_name, now=now)
        today = _today_str(self.tz_name, now=current)
        cookie_mtime = _get_mtime(cookie_path)

        effective_threshold = max(1, int(min_failures_to_pause or self.threshold))

        result = {
            "should_notify": False,
            "opened_circuit": False,
            "paused_until": None,
            "consecutive_failures": 0,
        }

        def _apply(entry: dict) -> dict:
            entry = dict(entry or {})
            previous_paused_until = _str_to_dt(entry.get("paused_until"))
            tz = _load_tz(self.tz_name)
            was_paused = bool(
                previous_paused_until
                and _to_aligned(current, tz) < _to_aligned(previous_paused_until, tz)
            )

            prev_mtime = entry.get("cookie_mtime")
            try:
                prev_mtime = float(prev_mtime) if prev_mtime is not None else None
            except (TypeError, ValueError):
                prev_mtime = None

            if cookie_path and _cookie_changed(cookie_path, prev_mtime):
                entry["consecutive_failures"] = 0
                entry["paused_until"] = None
                entry["last_notified_date"] = None

            consecutive = _as_int(entry.get("consecutive_failures"), 0) + 1
            entry["consecutive_failures"] = consecutive
            entry["last_failure_reason"] = (reason or "未知错误")[:1000]
            entry["last_failure_at"] = _dt_to_str(current)
            if cookie_path:
                entry["cookie_path"] = cookie_path
                if cookie_mtime is not None:
                    entry["cookie_mtime"] = cookie_mtime

            opened = False
            if consecutive >= effective_threshold:
                paused_until = current + timedelta(seconds=self.pause_seconds)
                entry["paused_until"] = _dt_to_str(paused_until)
                opened = not was_paused

                if entry.get("last_notified_date") != today:
                    entry["last_notified_date"] = today
                    result["should_notify"] = True

                result["paused_until"] = paused_until
            else:
                entry["paused_until"] = None

            result["opened_circuit"] = opened
            result["consecutive_failures"] = consecutive
            return entry

        self._update_task(task_key, _apply)
        return result
