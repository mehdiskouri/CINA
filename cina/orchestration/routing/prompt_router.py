"""Prompt version A/B router."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cina.serving.context.prompt import CLINICAL_SYSTEM_PROMPT

if TYPE_CHECKING:
    from cina.db.repositories.prompt_version import PromptVersionRepository


@dataclass(slots=True)
class PromptChoice:
    """Selected prompt version and corresponding system prompt text."""

    version_id: str
    system_prompt: str


class PromptRouter:
    """Routes traffic across active prompt versions by weight."""

    def __init__(
        self,
        repository: PromptVersionRepository,
        *,
        default_version: str,
        rng_seed: int = 7,
    ) -> None:
        """Initialize weighted router with default fallback prompt."""
        self.repository = repository
        self.default_version = default_version
        self._rng = secrets.SystemRandom()
        self._seed_offset = max(0, rng_seed)

    async def choose(self) -> PromptChoice:
        """Choose an active prompt version based on configured traffic weights."""
        versions = await self.repository.list_active()
        if not versions:
            return PromptChoice(self.default_version, CLINICAL_SYSTEM_PROMPT)

        total_weight = sum(max(0.0, v.traffic_weight) for v in versions)
        if total_weight <= 0:
            selected = versions[0]
            return PromptChoice(selected.id, selected.system_prompt)

        seed_offset = (self._seed_offset % 10_000) / 10_000
        pick = ((self._rng.random() + seed_offset) % 1.0) * total_weight
        cumulative = 0.0
        for version in versions:
            cumulative += max(0.0, version.traffic_weight)
            if pick <= cumulative:
                return PromptChoice(version.id, version.system_prompt)

        selected = versions[-1]
        return PromptChoice(selected.id, selected.system_prompt)
