"""OPC FastAPI 应用入口."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api_keys import router as api_keys_router
from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router, webhook_router as billing_webhook_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.projects import router as projects_router
from app.api.v1.preview import router as preview_router
from app.api.v1.webhooks import router as webhooks_router
from app.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.observability import init_sentry, metrics_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_sentry()
    logger = get_logger("app")
    logger.info("startup", app=settings.app_name, version=settings.version, debug=settings.debug)
    yield
    logger.info("shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(preview_router)
app.include_router(billing_router)
app.include_router(billing_webhook_router)
app.include_router(organizations_router)
app.include_router(api_keys_router)
app.include_router(webhooks_router)
app.include_router(metrics_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.version, "app": settings.app_name}


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.version,
        "docs": "/docs" if settings.debug else "disabled",
    }
