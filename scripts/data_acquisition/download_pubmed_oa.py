"""Download open-access PubMed XML full-text documents."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

TOPIC_QUERY = "(heart OR cancer OR infection OR diabetes)"
_HTTP_OK = 200


@dataclass(slots=True)
class _RunState:
    out_dir: Path
    downloaded: int
    scanned: int
    limit: int
    sleep_seconds: float


def _echo(message: str) -> None:
    """Write progress output to stdout for script execution."""
    sys.stdout.write(f"{message}\n")


def fetch_result_page(
    client: httpx.Client,
    cursor_mark: str | None,
    page_size: int,
) -> dict[str, object]:
    """Fetch one Europe PMC search result page."""
    query = f"OPEN_ACCESS:y AND {TOPIC_QUERY}"
    params: dict[str, str | int] = {
        "query": query,
        "format": "json",
        "pageSize": page_size,
        "resultType": "core",
    }
    if cursor_mark:
        params["cursorMark"] = cursor_mark

    response = client.get(SEARCH_URL, params=params, timeout=60.0)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return {}


def download_fulltext(client: httpx.Client, pmcid: str, out_dir: Path) -> bool:
    """Download a full-text XML document by PMCID if missing locally."""
    out_path = out_dir / f"{pmcid}.xml"
    if out_path.exists():
        return False

    response = client.get(FULLTEXT_URL.format(pmcid=quote(pmcid)), timeout=60.0)
    if response.status_code != _HTTP_OK or not response.text.strip().startswith("<"):
        return False

    out_path.write_text(response.text, encoding="utf-8")
    return True


def run(limit: int, out_dir: Path, page_size: int, sleep_seconds: float) -> None:
    """Download PubMed OA XML files up to the requested limit."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state = _RunState(
        out_dir=out_dir,
        downloaded=0,
        scanned=0,
        limit=limit,
        sleep_seconds=sleep_seconds,
    )
    cursor_mark: str | None = None

    with httpx.Client(follow_redirects=True) as client:
        while state.downloaded < limit:
            payload = fetch_result_page(client, cursor_mark, page_size)
            cursor_mark = _next_cursor_mark(payload)
            results = _result_rows(payload)
            if not results:
                break

            state = _process_result_batch(
                client=client,
                results=results,
                state=state,
            )

            if not cursor_mark:
                break

    _echo(
        f"DONE pubmed_oa downloaded={state.downloaded} scanned={state.scanned} out={out_dir}",
    )


def _next_cursor_mark(payload: dict[str, object]) -> str | None:
    """Extract the next cursor mark from a Europe PMC payload."""
    cursor_mark_raw = payload.get("nextCursorMark")
    return str(cursor_mark_raw) if isinstance(cursor_mark_raw, str) else None


def _result_rows(payload: dict[str, object]) -> list[object]:
    """Extract untyped result rows list from a Europe PMC payload."""
    result_list = payload.get("resultList", {})
    results_raw = result_list.get("result", []) if isinstance(result_list, dict) else []
    return results_raw if isinstance(results_raw, list) else []


def _process_result_batch(
    *,
    client: httpx.Client,
    results: list[object],
    state: _RunState,
) -> _RunState:
    """Process one result page and return updated downloaded/scanned counters."""
    for row in results:
        if not isinstance(row, dict):
            continue
        state.scanned += 1
        pmcid = row.get("pmcid")
        if not pmcid:
            continue
        if download_fulltext(client, str(pmcid), state.out_dir):
            state.downloaded += 1
            if state.downloaded % 100 == 0:
                _echo(f"downloaded={state.downloaded} scanned={state.scanned}")
        if state.downloaded >= state.limit:
            break
        if state.sleep_seconds > 0:
            time.sleep(state.sleep_seconds)
    return state


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for PubMed OA downloader."""
    parser = argparse.ArgumentParser(description="Download open-access PubMed XML data")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--out", type=Path, default=Path("data/pubmed"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        limit=args.limit,
        out_dir=args.out,
        page_size=args.page_size,
        sleep_seconds=args.sleep_seconds,
    )
