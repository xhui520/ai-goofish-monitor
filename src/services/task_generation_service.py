"""
任务生成作业服务
"""
import asyncio
from collections import deque
from copy import deepcopy
import threading
from typing import Awaitable, Dict, Iterable, Optional
from uuid import uuid4

from src.domain.models.task import Task
from src.domain.models.task_generation import TaskGenerationJob, TaskGenerationStep

# 仅保留最近 N 个已结束（完成/失败）的作业，避免 _jobs 无限增长
MAX_FINISHED_JOBS = 50

DEFAULT_GENERATION_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare", "接收创建请求"),
    ("reference", "读取参考文件"),
    ("prompt", "构建提示词"),
    ("llm", "调用 AI 生成标准"),
    ("persist", "保存分析标准"),
    ("task", "创建任务记录"),
)


class TaskGenerationService:
    """管理 AI 任务生成的后台作业状态"""

    def __init__(self, step_specs: Iterable[tuple[str, str]] = DEFAULT_GENERATION_STEPS):
        self._step_specs = tuple(step_specs)
        self._jobs: Dict[str, TaskGenerationJob] = {}
        self._lock = threading.Lock()
        self._workers: set[threading.Thread] = set()
        self._finished_job_ids: deque[str] = deque()
        self._keyword_locks: Dict[str, threading.Lock] = {}
        self._keyword_locks_guard = threading.Lock()

    def get_keyword_lock(self, keyword: str) -> threading.Lock:
        """获取同关键词生成作业的互斥锁（串行化相同关键词的生成任务）"""
        with self._keyword_locks_guard:
            lock = self._keyword_locks.get(keyword)
            if lock is None:
                lock = threading.Lock()
                self._keyword_locks[keyword] = lock
            return lock

    async def create_job(self, task_name: str) -> TaskGenerationJob:
        job = TaskGenerationJob(
            job_id=uuid4().hex,
            task_name=task_name,
            steps=[
                TaskGenerationStep(key=key, label=label)
                for key, label in self._step_specs
            ],
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._prune_finished_jobs_locked()
            return deepcopy(job)

    async def get_job(self, job_id: str) -> Optional[TaskGenerationJob]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return deepcopy(job)

    def track(self, coroutine: Awaitable[None]) -> None:
        thread: Optional[threading.Thread] = None

        def runner() -> None:
            try:
                asyncio.run(coroutine)
            finally:
                # thread 在 start 前必已赋值，这里直接判空兜底
                if thread is not None:
                    with self._lock:
                        self._workers.discard(thread)

        thread = threading.Thread(target=runner, daemon=True)
        with self._lock:
            self._workers.add(thread)
        thread.start()

    async def advance(self, job_id: str, step_key: str, message: str) -> TaskGenerationJob:
        with self._lock:
            job = self._require_job(job_id)
            target_index = self._find_step_index(job, step_key)
            job.status = "running"
            job.current_step = step_key
            job.message = message
            for index, step in enumerate(job.steps):
                if step.status == "failed":
                    continue
                if index < target_index:
                    step.status = "completed"
                elif index == target_index:
                    step.status = "running"
                    step.message = message
                elif step.status != "pending":
                    step.status = "pending"
                    step.message = ""
            return deepcopy(job)

    async def complete(self, job_id: str, task: Task, message: str) -> TaskGenerationJob:
        with self._lock:
            job = self._require_job(job_id)
            job.status = "completed"
            job.current_step = None
            job.message = message
            job.error = None
            job.task = task
            for step in job.steps:
                if step.status != "failed":
                    step.status = "completed"
            self._mark_finished_locked(job.job_id)
            return deepcopy(job)

    async def fail(
        self,
        job_id: str,
        error: str,
        step_key: Optional[str] = None,
    ) -> TaskGenerationJob:
        with self._lock:
            job = self._require_job(job_id)
            failed_step = step_key or job.current_step
            job.status = "failed"
            job.error = error
            job.message = error
            job.current_step = failed_step
            if failed_step:
                step = self._find_step(job, failed_step)
                if step:
                    step.status = "failed"
                    step.message = error
            self._mark_finished_locked(job.job_id)
            return deepcopy(job)

    def _mark_finished_locked(self, job_id: str) -> None:
        if job_id not in self._finished_job_ids:
            self._finished_job_ids.append(job_id)
        self._prune_finished_jobs_locked()

    def _prune_finished_jobs_locked(self) -> None:
        while len(self._finished_job_ids) > MAX_FINISHED_JOBS:
            oldest_job_id = self._finished_job_ids.popleft()
            self._jobs.pop(oldest_job_id, None)

    def _require_job(self, job_id: str) -> TaskGenerationJob:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"任务生成作业不存在: {job_id}")
        return job

    def _find_step(self, job: TaskGenerationJob, step_key: str) -> Optional[TaskGenerationStep]:
        for step in job.steps:
            if step.key == step_key:
                return step
        return None

    def _find_step_index(self, job: TaskGenerationJob, step_key: str) -> int:
        for index, step in enumerate(job.steps):
            if step.key == step_key:
                return index
        raise KeyError(f"未知的任务生成步骤: {step_key}")
