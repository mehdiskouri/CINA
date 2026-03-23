"""Health and readiness endpoints for service monitoring."""

from fastapi import APIRouter

from cina.db.connection import db_healthcheck

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    """Return liveness status including database health check payload."""
    db_status = await db_healthcheck()
    status = "healthy" if db_status.get("status") == "ok" else "unhealthy"
    return {"status": status, "checks": {"postgres": db_status}}


@router.get("/ready")
async def ready() -> dict[str, str]:
    """Return readiness state based on database reachability."""
    db_status = await db_healthcheck()
    if db_status.get("status") != "ok":
        return {"status": "not_ready"}
    return {"status": "ready"}
