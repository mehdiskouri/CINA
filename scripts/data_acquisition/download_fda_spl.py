"""Download FDA DailyMed SPL XML labels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

LIST_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
DETAIL_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml"
_HTTP_OK = 200


def _echo(message: str) -> None:
    """Write progress output to stdout for script execution."""
    sys.stdout.write(f"{message}\n")


def fetch_page(client: httpx.Client, page: int, page_size: int) -> list[dict[str, object]]:
    """Fetch one page of DailyMed SPL metadata rows."""
    response = client.get(LIST_URL, params={"page": page, "pagesize": page_size}, timeout=60.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", [])
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def download_spl(client: httpx.Client, setid: str, out_dir: Path) -> bool:
    """Download one SPL XML file by setid if not already present."""
    out_path = out_dir / f"{setid}.xml"
    if out_path.exists():
        return True
    response = client.get(DETAIL_URL.format(setid=setid), timeout=60.0)
    if response.status_code != _HTTP_OK or not response.text.strip().startswith("<"):
        return False
    out_path.write_text(response.text, encoding="utf-8")
    return True


def run(limit: int, out_dir: Path, page_size: int) -> None:
    """Download SPL XML files up to the requested limit."""
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    page = 1

    with httpx.Client(follow_redirects=True) as client:
        while downloaded < limit:
            rows = fetch_page(client, page=page, page_size=page_size)
            if not rows:
                break

            for row in rows:
                setid = row.get("setid")
                if not isinstance(setid, str) or not setid:
                    continue
                if download_spl(client, setid, out_dir):
                    downloaded += 1
                    if downloaded % 100 == 0:
                        _echo(f"downloaded={downloaded} page={page}")
                if downloaded >= limit:
                    break

            page += 1

    _echo(f"DONE fda_spl downloaded={downloaded} out={out_dir}")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for FDA SPL downloader."""
    parser = argparse.ArgumentParser(description="Download DailyMed SPL XML labels")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--out", type=Path, default=Path("data/fda"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(limit=args.limit, out_dir=args.out, page_size=args.page_size)
