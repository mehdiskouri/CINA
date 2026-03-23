"""Capture Grafana dashboard screenshots after warming metric activity."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from playwright.async_api import Page, async_playwright

DASHBOARDS = [
    ("cina-ingestion-pipeline", "ingestion-pipeline.png"),
    ("cina-query-performance", "query-performance.png"),
    ("cina-orchestration-health", "orchestration-health.png"),
    ("cina-cost-tracking", "cost-tracking.png"),
]

GRAFANA_BASE_URL = "http://localhost:3000"
API_BASE_URL = os.getenv("CINA_API_BASE_URL", "http://localhost:8000")
PROMETHEUS_BASE_URL = os.getenv("CINA_PROMETHEUS_BASE_URL", "http://localhost:9090")
WAIT_TIMEOUT_SECONDS = 90
WARMUP_REQUESTS = int(os.getenv("CINA_SCREENSHOT_WARMUP_REQUESTS", "8"))
WARMUP_SPACING_SECONDS = float(os.getenv("CINA_SCREENSHOT_WARMUP_SPACING_SECONDS", "4"))
_HTTP_BAD_REQUEST = 400
_WARMUP_TIMEOUT_SECONDS = 120
_PROM_QUERY_TIMEOUT_SECONDS = 15
_PANEL_POLL_SECONDS = 2
_PROM_VALUE_MIN_LEN = 2


def _echo(message: str) -> None:
    """Write progress output to stdout for script execution."""
    sys.stdout.write(f"{message}\n")


def _build_auth_header() -> dict[str, str]:
    """Build Authorization header for warmup API calls."""
    token = os.getenv("CINA_SCREENSHOT_API_KEY") or os.getenv("CINA_LOAD_API_KEY")
    auth_disabled = os.getenv("CINA_AUTH_DISABLED", "0") == "1"
    if token:
        return {"Authorization": f"Bearer {token}"}
    if auth_disabled:
        return {}
    msg = (
        "No API key found for screenshot warmup. Set CINA_SCREENSHOT_API_KEY "
        "(or CINA_LOAD_API_KEY), or set CINA_AUTH_DISABLED=1 for local dev."
    )
    raise RuntimeError(msg)


async def _warmup_metrics() -> None:
    """Generate request traffic so Prometheus-backed charts render non-zero data."""
    headers = {
        "Content-Type": "application/json",
        **_build_auth_header(),
    }

    prompts = [
        "What are common adverse effects of metformin?",
        "Summarize first-line treatment for metastatic breast cancer.",
        "What evidence supports PD-1 inhibitors in melanoma?",
    ]

    async with httpx.AsyncClient(timeout=httpx.Timeout(_WARMUP_TIMEOUT_SECONDS)) as client:
        for i in range(WARMUP_REQUESTS):
            query = prompts[i % len(prompts)]
            response = await client.post(
                f"{API_BASE_URL}/v1/query",
                json={"query": query},
                headers=headers,
            )
            if response.status_code >= _HTTP_BAD_REQUEST:
                msg = f"Warmup query failed with status {response.status_code}"
                raise RuntimeError(msg)
            if i < WARMUP_REQUESTS - 1:
                await asyncio.sleep(WARMUP_SPACING_SECONDS)

    await asyncio.sleep(20)


async def _prom_scalar(query: str) -> float:
    """Evaluate a Prometheus scalar query result."""
    params = {"query": query}
    async with httpx.AsyncClient(timeout=httpx.Timeout(_PROM_QUERY_TIMEOUT_SECONDS)) as client:
        response = await client.get(f"{PROMETHEUS_BASE_URL}/api/v1/query", params=params)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        return 0.0
    data = payload.get("data", {})
    if not isinstance(data, dict):
        return 0.0
    result = data.get("result", [])
    if not isinstance(result, list) or not result:
        return 0.0
    first = result[0]
    if not isinstance(first, dict):
        return 0.0
    value = first.get("value", [])
    if not isinstance(value, list) or len(value) < _PROM_VALUE_MIN_LEN:
        return 0.0
    return float(value[1])


async def _assert_metric_activity() -> None:
    """Fail fast if warmup did not produce expected Prometheus activity."""
    checks = {
        "query": "sum(rate(cina_query_total[5m]))",
        "provider": "sum(rate(cina_provider_request_total[5m]))",
        "cost": "sum(rate(cina_cost_usd_total[5m]))",
    }
    values = {name: await _prom_scalar(promql) for name, promql in checks.items()}

    if values["query"] <= 0:
        msg = "No query traffic observed in Prometheus. Warmup could not generate query activity."
        raise RuntimeError(msg)
    if values["provider"] <= 0:
        msg = (
            "Query traffic exists but provider metrics are still zero. "
            "Start the API with real provider keys loaded "
            "(e.g. env.keys.local) and rerun capture."
        )
        raise RuntimeError(msg)


async def _wait_for_dashboard_data(page: Page) -> None:
    """Wait until the Grafana panel content is rendered and not empty."""

    async def _panel_ready() -> bool:
        loading = page.locator("text=/Loading|Refreshing/i")
        if await loading.count() > 0:
            return False

        no_data = page.locator("text=/No data|No Data/i")
        if await no_data.count() > 0:
            return False

        rendered = page.locator(
            ".uplot, .panel-content canvas, .panel-content svg, "
            '.panel-content [data-testid="stat-panel-value"]',
        )
        return await rendered.count() > 0

    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _panel_ready():
            return
        await asyncio.sleep(_PANEL_POLL_SECONDS)

    msg = "Timed out waiting for Grafana panels to render data"
    raise TimeoutError(msg)


async def main() -> None:
    """Warm up traffic and capture screenshot artifacts from Grafana."""
    output_dir = Path("docs/screenshots")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

    _echo("Warming up API and metrics...")
    await _warmup_metrics()
    await _assert_metric_activity()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})

        await page.goto(f"{GRAFANA_BASE_URL}/login", wait_until="domcontentloaded")
        await page.fill('input[name="user"]', "admin")
        await page.fill('input[name="password"]', "admin")
        await page.locator('button[type="submit"]').click()
        await page.wait_for_load_state("networkidle")

        password_update = page.locator("text=/Update your password/i")
        if await password_update.count() > 0:
            await page.locator('button:has-text("Skip")').click()
            await page.wait_for_load_state("networkidle")

        if "/login" in page.url:
            await page.screenshot(path=str(output_dir / "_debug_login_failed.png"), full_page=True)
            msg = "Grafana login failed; verify admin credentials and login form state"
            raise RuntimeError(msg)

        for uid, filename in DASHBOARDS:
            dashboard_url = f"{GRAFANA_BASE_URL}/d/{uid}?orgId=1&from=now-15m&to=now&refresh=5s"
            await page.goto(dashboard_url, wait_until="networkidle")
            await _wait_for_dashboard_data(page)
            await page.screenshot(path=str(output_dir / filename), full_page=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
