from __future__ import annotations

import os
import random
from typing import ClassVar

from locust import HttpUser, between, task


class CINAQueryUser(HttpUser):
    wait_time = between(0.8, 1.2)

    repeated_queries: ClassVar[list[str]] = [
        "What is first-line therapy for metastatic breast cancer?",
        "What are common metformin adverse effects?",
        "How effective are PD-1 inhibitors in melanoma?",
    ]
    unique_templates: ClassVar[list[str]] = [
        "Summarize trial outcomes for condition {}",
        "What evidence supports treatment {} in oncology?",
        "List contraindications for medication {}",
    ]

    def on_start(self) -> None:
        self.api_key = os.getenv("CINA_LOAD_API_KEY", "")
        self.failover_mode = os.getenv("CINA_LOAD_FAILOVER_MODE", "0") == "1"

    @task(3)
    def query_repeated(self) -> None:
        self._send_query(random.choice(self.repeated_queries))

    @task(2)
    def query_unique(self) -> None:
        term = str(random.randint(1, 5000))
        prompt = random.choice(self.unique_templates).format(term)
        self._send_query(prompt)

    def _send_query(self, query: str) -> None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Optional knob for orchestrated failover experiments during load tests.
        if self.failover_mode:
            headers["X-CINA-Failover-Test"] = "1"

        self.client.post(
            "/v1/query",
            json={"query": query},
            headers=headers,
            name="/v1/query",
            timeout=120,
        )
