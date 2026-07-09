"""应用配置 - 通过环境变量加载，pydantic-settings 自动验证类型。

不写死任何 URL / API key。所有敏感配置走 .env 环境变量。
"""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "OPC - One Prompt Creates"
    version: str = "0.1.0"
    debug: bool = False

    # CORS（前端开发服务器地址）
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Database (Postgres + asyncpg)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/opc"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # LLM Provider: anthropic or openai
    llm_provider: str = "openai"

    # OpenAI-compatible providers (Agnes, etc.)
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("openai_api_key", "agnes_api_key", "llm_api_key"),
    )
    openai_base_url: str = Field(
        default="https://apihub.agnes-ai.com/v1",
        validation_alias=AliasChoices("openai_base_url", "agnes_base_url", "llm_base_url"),
    )
    openai_model: str = Field(
        default="agnes-2.0-flash",
        validation_alias=AliasChoices("openai_model", "agnes_model", "llm_model"),
    )
    # 是否启用 thinking 模式（Agnes 编码/推理任务推荐开启）
    openai_enable_thinking: bool = Field(
        default=False,
        validation_alias=AliasChoices("openai_enable_thinking", "agnes_enable_thinking"),
    )
    openai_thinking_budget_tokens: int = Field(
        default=2048,
        validation_alias=AliasChoices("openai_thinking_budget_tokens", "agnes_thinking_budget_tokens"),
    )

    # Anthropic / Claude protocol (kept for future switching)
    anthropic_api_key: str = ""
    anthropic_model_haiku: str = "claude-haiku-4-5"
    anthropic_model_sonnet: str = "claude-sonnet-4-6"
    anthropic_model_opus: str = "claude-opus-4-7"

    # MiniMax M3 (Anthropic-compatible API)
    minimax_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("minimax_api_key", "MINIMAX_API_KEY"),
    )
    minimax_base_url: str = Field(
        default="https://api.minimaxi.com/anthropic",
        validation_alias=AliasChoices("minimax_base_url", "MINIMAX_BASE_URL"),
    )
    minimax_model: str = Field(
        default="MiniMax-M3",
        validation_alias=AliasChoices("minimax_model", "MINIMAX_MODEL"),
    )

    # Agent
    agent_max_iterations: int = 20
    agent_max_tokens_per_call: int = 4096
    agent_score_threshold: float = 7.0
    # 单次 LLM 调用的硬超时（秒）。SDK 默认 60s 容易误杀慢但成功的调用。
    # 改成 180s 让 retry/fallback 能在合理时间内接管,而不是卡到用户重试。
    llm_call_timeout_seconds: int = Field(
        default=180,
        validation_alias=AliasChoices(
            "llm_call_timeout_seconds", "OPC_LLM_TIMEOUT_SECONDS"
        ),
    )

    # === P1-4: LLM 成本控制 (上线收费必做) ===
    # 单项目 LLM 调用累计 cost 告警阈值 (USD)
    # 超过此值, orchestrator 会切换到 Lite model 跑后续阶段
    cost_alert_threshold_usd: float = Field(
        default=0.50,
        validation_alias=AliasChoices(
            "cost_alert_threshold_usd", "OPC_COST_ALERT_THRESHOLD_USD"
        ),
    )
    # 单项目 LLM 调用硬上限 (USD) — 超过就 abort, 防 LLM loop 烧光额度
    cost_hard_limit_usd: float = Field(
        default=2.00,
        validation_alias=AliasChoices(
            "cost_hard_limit_usd", "OPC_COST_HARD_LIMIT_USD"
        ),
    )

    # 生成项目存储路径
    generated_projects_dir: str = "generated-projects"

    # Stripe (subscriptions + customer portal)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_lite_monthly: str = ""
    stripe_price_lite_yearly: str = ""
    stripe_price_pro_monthly: str = ""
    stripe_price_pro_yearly: str = ""
    stripe_price_max_monthly: str = ""
    stripe_price_max_yearly: str = ""
    # 前端域名, 用于 Stripe Checkout 成功/取消跳转
    app_base_url: str = "http://localhost:3000"

    # Sentry (错误监控, 留空则不启用)
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1

    # JWT
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7


settings = Settings()
