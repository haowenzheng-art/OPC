"""Observability setup - Sentry + Prometheus.

设计:
1. Sentry 捕获所有未捕获异常 + 性能追踪
2. Prometheus 暴露 /metrics 端点, 给 Prometheus server 抓取
3. 自定义业务指标: projects_created_total, credits_used_total, llm_tokens_total
"""
from __future__ import annotations

import sentry_sdk
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from fastapi import APIRouter, Response

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


# ============ 自定义 Prometheus 指标 ============

REGISTRY = CollectorRegistry()

# 业务指标
PROJECTS_CREATED = Counter(
    "opc_projects_created_total",
    "Total projects created",
    ["plan"],
    registry=REGISTRY,
)

PROJECTS_COMPLETED = Counter(
    "opc_projects_completed_total",
    "Total projects completed successfully",
    ["plan"],
    registry=REGISTRY,
)

PROJECTS_FAILED = Counter(
    "opc_projects_failed_total",
    "Total projects failed",
    ["plan"],
    registry=REGISTRY,
)

CREDITS_USED = Counter(
    "opc_credits_used_total",
    "Total credits consumed",
    ["plan"],
    registry=REGISTRY,
)

LLM_TOKENS = Counter(
    "opc_llm_tokens_total",
    "Total LLM tokens used",
    ["type", "model"],  # type=input/output
    registry=REGISTRY,
)

# HTTP 指标
HTTP_REQUESTS = Counter(
    "opc_http_requests_total",
    "HTTP requests total",
    ["method", "endpoint", "status"],
    registry=REGISTRY,
)

HTTP_DURATION = Histogram(
    "opc_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    registry=REGISTRY,
)

# Active metrics
ACTIVE_PROJECTS = Gauge(
    "opc_active_projects",
    "Currently active (non-completed) projects",
    registry=REGISTRY,
)


# ============ Router ============

metrics_router = APIRouter(tags=["metrics"])


@metrics_router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint (供 Prometheus 抓取)."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


# ============ Sentry 初始化 ============

def init_sentry() -> None:
    """初始化 Sentry. 如果 SENTRY_DSN 未配置, 跳过."""
    if not settings.sentry_dsn:
        log.info("sentry_disabled", msg="SENTRY_DSN not set, skipping")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,  # 不发送用户隐私
    )
    log.info("sentry_initialized", environment=settings.sentry_environment)
