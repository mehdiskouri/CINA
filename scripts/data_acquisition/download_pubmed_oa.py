#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path
from urllib.parse import quote

import httpx

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

TOPIC_QUERY = "(heart OR cancer OR infection OR diabetes)"


def fetch_result_page(client: httpx.Client, cursor_mark: str | None, page_size: int) -> dict:
    query = f"OPEN_ACCESS:y AND {TOPIC_QUERY}"
    params = {
        "query": query,
        "format": "json",
        "pageSize": page_size,
        "resultType": "core",
    }
    if cursor_mark:
        params["cursorMark"] = cursor_mark

    response = client.get(SEARCH_URL, params=params, timeout=60.0)
    response.raise_for_status()
    return response.json()


def download_fulltext(client: httpx.Client, pmcid: str, out_dir: Path) -> bool:
    out_path = out_dir / f"{pmcid}.xml"
    if out_path.exists():
        return False

    response = client.get(FULLTEXT_URL.format(pmcid=quote(pmcid)), timeout=60.0)
    if response.status_code != 200 or not response.text.strip().startswith("<"):
        return False

    out_path.write_text(response.text, encoding="utf-8")
    return True


def run(limit: int, out_dir: Path, page_size: int, sleep_seconds: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    scanned = 0
    cursor_mark: str | None = None

    with httpx.Client(follow_redirects=True) as client:
        while downloaded < limit:
            payload = fetch_result_page(client, cursor_mark, page_size)
            cursor_mark = payload.get("nextCursorMark")
            results = payload.get("resultList", {}).get("result", [])
            if not results:
                break

            for row in results:
                scanned += 1
                pmcid = row.get("pmcid")
                if not pmcid:
                    continue
                if download_fulltext(client, pmcid, out_dir):
                    downloaded += 1
                    if downloaded % 100 == 0:
                        print(f"downloaded={downloaded} scanned={scanned}")
                if downloaded >= limit:
                    break
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

            if not cursor_mark:
                break

    print(f"DONE pubmed_oa downloaded={downloaded} scanned={scanned} out={out_dir}")


def parse_args() -> argparse.Namespace:
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
