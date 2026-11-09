"""
调度服务
负责管理定时任务的调度
"""
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import List

from src.core.cron_utils import build_cron_trigger
from src.domain.models.task import Task
from src.services.process_service import ProcessService


RELOAD_FROM_THREAD_TIMEOUT_SECONDS = 60


class SchedulerService:
    """调度服务"""

    def __init__(self, process_service: ProcessService):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self.process_service = process_service
        # 调度器启动所在的事件循环；AsyncIOScheduler 只能在该循环线程上操作
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        """启动调度器（必须在调度器所属的事件循环线程上调用）"""
        if not self.scheduler.running:
            self._loop = asyncio.get_running_loop()
            self.scheduler.start()
            print("调度器已启动")

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            print("调度器已停止")

    def get_next_run_time(self, task_id: int):
        job = self.scheduler.get_job(f"task_{task_id}")
        if job is None:
            return None

        next_run_time = getattr(job, "next_run_time", None)
        if next_run_time is not None:
            return next_run_time

        trigger = getattr(job, "trigger", None)
        if trigger is None or not hasattr(trigger, "get_next_fire_time"):
            return None

        try:
            now = datetime.now(self.scheduler.timezone)
            return trigger.get_next_fire_time(None, now)
        except Exception:
            return None

    async def reload_jobs(self, tasks: List[Task]):
        """重新加载所有定时任务。

        AsyncIOScheduler 只允许在启动它的事件循环线程上操作。若本方法在
        其他线程（如 AI 任务生成工作线程）中被调用，会自动把真正的重载
        调度回启动循环执行，避免跨线程操作 APScheduler 内部状态。
        """
        try:
            current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._loop is not None and current_loop is not self._loop:
            if current_loop is not None:
                # 在另一个事件循环中：转发到启动循环并等待完成
                future = asyncio.run_coroutine_threadsafe(
                    self._reload_jobs_impl(tasks), self._loop
                )
                try:
                    await asyncio.wait_for(
                        asyncio.wrap_future(future),
                        timeout=RELOAD_FROM_THREAD_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    print("警告: 调度器跨循环重载等待超时，稍后可手动刷新任务列表")
            else:
                # 在没有事件循环的线程中：阻塞等待启动循环完成
                asyncio.run_coroutine_threadsafe(
                    self._reload_jobs_impl(tasks), self._loop
                ).result(timeout=RELOAD_FROM_THREAD_TIMEOUT_SECONDS)
            return

        if self._loop is None:
            print(
                "[警告] 调度器尚未启动，本次重载将在当前循环执行；"
                "建议在应用 lifespan 启动调度器后再触发重载。"
            )
        await self._reload_jobs_impl(tasks)

    async def _reload_jobs_impl(self, tasks: List[Task]):
        """重新加载所有定时任务（仅允许在调度器所属循环线程上执行）"""
        print("正在重新加载定时任务...")
        self.scheduler.remove_all_jobs()

        for task in tasks:
            if task.enabled and task.cron:
                try:
                    trigger = build_cron_trigger(
                        task.cron,
                        timezone=self.scheduler.timezone,
                    )
                    self.scheduler.add_job(
                        self._run_task,
                        trigger=trigger,
                        args=[task.id, task.task_name],
                        id=f"task_{task.id}",
                        name=f"Scheduled: {task.task_name}",
                        replace_existing=True
                    )
                    print(f"  -> 已为任务 '{task.task_name}' 添加定时规则: '{task.cron}'")
                except ValueError as e:
                    print(f"  -> [警告] 任务 '{task.task_name}' 的 Cron 表达式无效: {e}")

        print("定时任务加载完成")

    async def _run_task(self, task_id: int, task_name: str):
        """执行定时任务"""
        print(f"定时任务触发: 正在为任务 '{task_name}' 启动爬虫...")
        await self.process_service.start_task(task_id, task_name)
