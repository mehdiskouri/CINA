"""Prompt version A/B router."""

from __future__ import annotations

import random
from dataclasses import dataclass

from cina.db.repositories.prompt_version import PromptVersionRepository
from cina.serving.context.prompt import CLINICAL_SYSTEM_PROMPT


@dataclass(slots=True)
class PromptChoice:
    version_id: str
    system_prompt: str


class PromptRouter:
    def __init__(
        self,
        repository: PromptVersionRepository,
        *,
        default_version: str,
        rng_seed: int = 7,
    ) -> None:
        self.repository = repository
        self.default_version = default_version
        self._rng = random.Random(rng_seed)

    async def choose(self) -> PromptChoice:
        versions = await self.repository.list_active()
        if not versions:
            return PromptChoice(self.default_version, CLINICAL_SYSTEM_PROMPT)

        total_weight = sum(max(0.0, v.traffic_weight) for v in versions)
        if total_weight <= 0:
            selected = versions[0]
            return PromptChoice(selected.id, selected.system_prompt)

        pick = self._rng.random() * total_weight
        cumulative = 0.0
        for version in versions:
            cumulative += max(0.0, version.traffic_weight)
            if pick <= cumulative:
                return PromptChoice(version.id, version.system_prompt)

        selected = versions[-1]
        return PromptChoice(selected.id, selected.system_prompt)
