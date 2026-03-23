"""Download ClinicalTrials.gov records as JSON documents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
_HTTP_FORBIDDEN = 403


def _echo(message: str) -> None:
    """Write progress output to stdout for script execution."""
    sys.stdout.write(f"{message}\n")


def fetch_page(
    client: httpx.Client,
    page_size: int,
    next_page_token: str | None,
    topic_query: str | None,
) -> dict[str, object]:
    """Fetch one studies result page with optional topic filtering."""
    params: dict[str, str | int] = {
        "pageSize": page_size,
    }
    if topic_query:
        params["query.term"] = topic_query
    if next_page_token:
        params["pageToken"] = next_page_token

    response = client.get(BASE_URL, params=params, timeout=60.0)
    if response.status_code == _HTTP_FORBIDDEN and topic_query:
        # Some API edge/front-door configurations reject filtered queries from shared IPs.
        fallback_params: dict[str, str | int] = {"pageSize": page_size}
        if next_page_token:
            fallback_params["pageToken"] = next_page_token
        response = client.get(BASE_URL, params=fallback_params, timeout=60.0)

    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return {}


def run(limit: int, out_dir: Path, page_size: int, topic_query: str | None) -> None:
    """Download study JSON files up to the requested limit."""
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    token: str | None = None

    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        while downloaded < limit:
            payload = fetch_page(
                client,
                page_size=page_size,
                next_page_token=token,
                topic_query=topic_query,
            )
            studies_raw = payload.get("studies", [])
            studies = studies_raw if isinstance(studies_raw, list) else []
            if not studies:
                break

            downloaded = _download_studies_batch(
                studies=studies,
                out_dir=out_dir,
                downloaded=downloaded,
                limit=limit,
            )

            next_token = payload.get("nextPageToken")
            token = str(next_token) if isinstance(next_token, str) else None
            if not token:
                break

    _echo(f"DONE clinicaltrials downloaded={downloaded} out={out_dir}")


def _download_studies_batch(
    *,
    studies: list[object],
    out_dir: Path,
    downloaded: int,
    limit: int,
) -> int:
    """Persist one page of study payloads and return updated download count."""
    for study in studies:
        if not isinstance(study, dict):
            continue
        identification_raw = study.get("protocolSection", {})
        identification = (
            identification_raw.get("identificationModule", {})
            if isinstance(identification_raw, dict)
            else {}
        )
        if not isinstance(identification, dict):
            continue
        nct_id = identification.get("nctId")
        if not nct_id:
            continue

        out_path = out_dir / f"{nct_id}.json"
        if not out_path.exists():
            out_path.write_text(json.dumps(study, ensure_ascii=True), encoding="utf-8")
            downloaded += 1
            if downloaded % 100 == 0:
                _echo(f"downloaded={downloaded}")
        if downloaded >= limit:
            break
    return downloaded


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for clinical trials downloader."""
    parser = argparse.ArgumentParser(description="Download ClinicalTrials.gov JSON records")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--topic-query",
        type=str,
        default="heart OR cancer OR infection OR diabetes",
    )
    parser.add_argument("--out", type=Path, default=Path("data/clinicaltrials"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        limit=args.limit,
        out_dir=args.out,
        page_size=args.page_size,
        topic_query=args.topic_query,
    )
