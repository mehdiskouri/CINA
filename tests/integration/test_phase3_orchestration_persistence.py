from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import bcrypt
import pytest

from cina.cli.db import run_migrations
from cina.config import clear_config_cache
from cina.db.connection import close_pool, get_pool
from cina.db.repositories.apikey import APIKeyRepository
from cina.db.repositories.cost_event import CostEventRepository
from cina.db.repositories.prompt_version import PromptVersionRepository
from cina.db.repositories.query_log import QueryLogRepository
from cina.serving.context.prompt import CLINICAL_SYSTEM_PROMPT

DEFAULT_DSN = "postgresql://cina:cina_dev@localhost:5432/cina"


async def _db_available(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.mark.asyncio
async def test_phase3_apikey_lifecycle_and_persistence() -> None:
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    if not await _db_available(dsn):
        pytest.skip("Postgres is not reachable for integration test")

    os.environ["DATABASE_URL"] = dsn
    clear_config_cache()
    await close_pool()
    await run_migrations()
    pool = await get_pool()

    api_repo = APIKeyRepository(pool)

    plain = "cina_sk_integration_lifecycle"
    key_hash = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE tenant_id = 'it-phase3'")

    key_id = await api_repo.create_key(key_hash=key_hash, tenant_id="it-phase3", name="k1")
    found = await api_repo.validate_token(plain)
    assert found is not None
    assert found.tenant_id == "it-phase3"

    rows = await api_repo.list_keys("it-phase3")
    assert any(str(r["id"]) == str(key_id) for r in rows)

    revoked = await api_repo.revoke_key(str(key_id))
    assert revoked is True
    assert await api_repo.validate_token(plain) is None

    await close_pool()


@pytest.mark.asyncio
async def test_phase3_query_and_cost_event_fk_chain() -> None:
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    if not await _db_available(dsn):
        pytest.skip("Postgres is not reachable for integration test")

    os.environ["DATABASE_URL"] = dsn
    clear_config_cache()
    await close_pool()
    await run_migrations()
    pool = await get_pool()

    prompt_repo = PromptVersionRepository(pool)
    query_repo = QueryLogRepository(pool)
    cost_repo = CostEventRepository(pool)

    await prompt_repo.upsert(
        version_id="v1.0",
        system_prompt=CLINICAL_SYSTEM_PROMPT,
        description="integration default",
        traffic_weight=1.0,
        active=True,
    )

    query_id = str(uuid4())
    await query_repo.insert(
        query_id=query_id,
        query_text="integration phase3",
        prompt_version_id="v1.0",
        provider_used="anthropic",
        fallback_triggered=False,
        cache_hit=False,
        total_latency_ms=100,
        search_latency_ms=20,
        rerank_latency_ms=30,
        llm_latency_ms=50,
        chunks_retrieved=10,
        chunks_used=3,
        tenant_id="it-phase3",
    )

    await cost_repo.insert(
        query_id=query_id,
        tenant_id="it-phase3",
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.001,
        cache_hit=False,
    )

    async with pool.acquire() as conn:
        q_count = await conn.fetchval(
            "SELECT count(*) FROM query_logs WHERE id = $1::uuid",
            query_id,
        )
        c_count = await conn.fetchval(
            "SELECT count(*) FROM cost_events WHERE query_id = $1::uuid",
            query_id,
        )

    assert int(q_count) == 1
    assert int(c_count) == 1
    await close_pool()
