#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"


def fetch_page(
    session: requests.Session,
    page_size: int,
    next_page_token: str | None,
    topic_query: str | None,
) -> dict:
    params: dict[str, str | int] = {
        "pageSize": page_size,
    }
    if topic_query:
        params["query.term"] = topic_query
    if next_page_token:
        params["pageToken"] = next_page_token

    response = session.get(BASE_URL, params=params, timeout=60.0)
    if response.status_code == 403 and topic_query:
        # Some API edge/front-door configurations reject filtered queries from shared IPs.
        fallback_params: dict[str, str | int] = {"pageSize": page_size}
        if next_page_token:
            fallback_params["pageToken"] = next_page_token
        response = session.get(BASE_URL, params=fallback_params, timeout=60.0)

    response.raise_for_status()
    return response.json()


def run(limit: int, out_dir: Path, page_size: int, topic_query: str | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    token: str | None = None

    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.Session() as session:
        session.headers.update(headers)
        while downloaded < limit:
            payload = fetch_page(
                session,
                page_size=page_size,
                next_page_token=token,
                topic_query=topic_query,
            )
            studies = payload.get("studies", [])
            if not studies:
                break

            for study in studies:
                identification = (
                    study.get("protocolSection", {})
                    .get("identificationModule", {})
                )
                nct_id = identification.get("nctId")
                if not nct_id:
                    continue

                out_path = out_dir / f"{nct_id}.json"
                if not out_path.exists():
                    out_path.write_text(json.dumps(study, ensure_ascii=True), encoding="utf-8")
                    downloaded += 1
                    if downloaded % 100 == 0:
                        print(f"downloaded={downloaded}")
                if downloaded >= limit:
                    break

            token = payload.get("nextPageToken")
            if not token:
                break

    print(f"DONE clinicaltrials downloaded={downloaded} out={out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download ClinicalTrials.gov JSON records")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--topic-query", type=str, default="heart OR cancer OR infection OR diabetes")
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
