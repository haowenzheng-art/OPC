"""Celery app 配置.

Broker + result backend 都用 Redis (port 6380, 避让 mediapilot-redis).
Worker 启动: uv run celery -A app.worker.celery_app worker --loglevel=info
"""
from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "opc",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.opc_tasks"],
)

celery_app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    timezone="UTC",
    enable_utc=True,
    # 默认超时 (单任务最长 10 分钟, 跟 LLM 长任务匹配)
    task_time_limit=600,
    task_soft_time_limit=540,
    # 失败重试 1 次 (再多说明有 bug)
    task_default_max_retries=1,
    # 默认队列
    task_default_queue="opc_default",
    # 任务完成结果保留 24h (够前端轮询 + 调试用)
    result_expires=86400,
)

# 自动发现 tasks 模块
celery_app.autodiscover_tasks(["app.worker"])
