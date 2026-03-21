from fastapi import FastAPI

from cina.api.middleware.auth import APIKeyAuthMiddleware
from cina.api.middleware.correlation import CorrelationIDMiddleware
from cina.api.middleware.rate_limit import RateLimitMiddleware
from cina.api.routes.health import router as health_router
from cina.api.routes.metrics import router as metrics_router
from cina.api.routes.query import router as query_router
from cina.config import load_config
from cina.db.connection import lifespan
from cina.observability.logging import configure_logging


def create_app() -> FastAPI:
    cfg = load_config()
    configure_logging(cfg.observability.log_level)

    app = FastAPI(title="CINA", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(query_router)
    return app


app = create_app()
