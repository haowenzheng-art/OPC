"""结构化日志配置 (structlog + JSON 输出)。

生产环境日志必须是结构化 JSON，方便 ELK 采集和查询。
"""
import logging
import sys

import structlog


def setup_logging():
    """初始化结构化日志，输出 JSON 到 stdout。"""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
