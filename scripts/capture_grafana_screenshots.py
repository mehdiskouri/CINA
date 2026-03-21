import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

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


def _build_auth_header() -> dict[str, str]:
    token = os.getenv("CINA_SCREENSHOT_API_KEY") or os.getenv("CINA_LOAD_API_KEY")
    auth_disabled = os.getenv("CINA_AUTH_DISABLED", "0") == "1"
    if token:
        return {"Authorization": f"Bearer {token}"}
    if auth_disabled:
        return {}
    raise RuntimeError(
        "No API key found for screenshot warmup. Set CINA_SCREENSHOT_API_KEY "
        "(or CINA_LOAD_API_KEY), or set CINA_AUTH_DISABLED=1 for local dev."
    )


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

    def _post_query_sync(query: str) -> None:
        payload = json.dumps({"query": query}).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE_URL}/v1/query",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"Warmup query failed with status {resp.status}")
                # Fully consume SSE stream so downstream provider/cost hooks execute.
                _ = resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Warmup query failed with status {exc.code}") from exc

    # Spread requests out so Prometheus rate()/increase() windows observe changes.
    for i in range(WARMUP_REQUESTS):
        await asyncio.to_thread(_post_query_sync, prompts[i % len(prompts)])
        if i < WARMUP_REQUESTS - 1:
            await asyncio.sleep(WARMUP_SPACING_SECONDS)

    # Let at least one additional scrape happen after final warmup request.
    await asyncio.sleep(20)


def _prom_scalar(query: str) -> float:
    url = (
        f"{PROMETHEUS_BASE_URL}/api/v1/query?"
        f"{urllib.parse.quote('query')}={urllib.parse.quote(query)}"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    result = payload.get("data", {}).get("result", [])
    if not result:
        return 0.0
    return float(result[0]["value"][1])


async def _assert_metric_activity() -> None:
    checks = {
        "query": "sum(rate(cina_query_total[5m]))",
        "provider": "sum(rate(cina_provider_request_total[5m]))",
        "cost": "sum(rate(cina_cost_usd_total[5m]))",
    }
    values = {
        name: await asyncio.to_thread(_prom_scalar, promql) for name, promql in checks.items()
    }

    if values["query"] <= 0:
        raise RuntimeError(
            "No query traffic observed in Prometheus. Warmup could not generate query activity."
        )
    if values["provider"] <= 0:
        raise RuntimeError(
            "Query traffic exists but provider metrics are still zero. "
            "Start the API with real provider keys loaded (e.g. env.keys.local) and rerun capture."
        )


async def _wait_for_dashboard_data(page) -> None:
    """Wait until the Grafana panel content is rendered and not empty."""

    async def _panel_ready() -> bool:
        loading = page.locator("text=/Loading|Refreshing/i")
        if await loading.count() > 0:
            return False

        no_data = page.locator("text=/No data|No Data/i")
        if await no_data.count() > 0:
            return False

        # Grafana 12 renders timeseries panels with uPlot divs.
        rendered = page.locator(
            '.uplot, .panel-content canvas, .panel-content svg, .panel-content [data-testid="stat-panel-value"]'
        )
        return await rendered.count() > 0

    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _panel_ready():
            return
        await asyncio.sleep(2)

    raise TimeoutError("Timed out waiting for Grafana panels to render data")


async def main() -> None:
    output_dir = Path("docs/screenshots")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

    await _warmup_metrics()
    await _assert_metric_activity()

    async with async_playwright() as p:
        browser = await p.chromium.launch()
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
            raise RuntimeError(
                "Grafana login failed; verify admin credentials and login form state"
            )

        for uid, filename in DASHBOARDS:
            dashboard_url = f"{GRAFANA_BASE_URL}/d/{uid}?orgId=1&from=now-15m&to=now&refresh=5s"
            await page.goto(dashboard_url, wait_until="networkidle")
            await _wait_for_dashboard_data(page)
            await page.screenshot(path=str(output_dir / filename), full_page=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
