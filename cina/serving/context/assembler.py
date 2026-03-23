"""Token-budget context assembly with greedy skip-and-try packing."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import tiktoken

from cina.observability.logging import get_logger
from cina.observability.metrics import cina_context_chunks_included, cina_context_tokens_used

if TYPE_CHECKING:
    from cina.models.search import SearchResult

log = get_logger("cina.serving.context.assembler")


@dataclass(slots=True)
class NumberedSource:
    """A search result with a 1-based citation index."""

    index: int
    chunk: SearchResult


@dataclass(slots=True)
class ContextBudget:
    """Token budget calculator for context window packing."""

    model_context_limit: int
    system_prompt_tokens: int
    query_tokens: int
    generation_buffer: int = 2048
    max_chunks: int = 15

    @property
    def available(self) -> int:
        """Return remaining token budget available for retrieved chunks."""
        remaining = (
            self.model_context_limit
            - self.system_prompt_tokens
            - self.query_tokens
            - self.generation_buffer
        )
        return max(0, remaining)


@lru_cache(maxsize=1)
def _get_encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens for a text value using the serving tokenizer."""
    return len(_get_encoder().encode(text))


def assemble_context(
    ranked_chunks: list[SearchResult],
    budget: ContextBudget,
) -> list[NumberedSource]:
    """Greedy skip-and-try packing of chunks within token budget.

    If a large chunk doesn't fit, smaller subsequent chunks may still be
    included.  This maximises information density within the budget.
    """
    sources: list[NumberedSource] = []
    tokens_used = 0

    for chunk in ranked_chunks:
        if len(sources) >= budget.max_chunks:
            break
        if tokens_used + chunk.token_count > budget.available:
            continue  # skip-and-try
        sources.append(NumberedSource(index=len(sources) + 1, chunk=chunk))
        tokens_used += chunk.token_count

    cina_context_tokens_used.observe(tokens_used)
    cina_context_chunks_included.observe(len(sources))
    log.debug(
        "context_assembled",
        chunks=len(sources),
        tokens_used=tokens_used,
        budget_available=budget.available,
    )
    return sources


def build_citations(sources: list[NumberedSource]) -> list[dict[str, object]]:
    """Extract citation metadata from assembled sources for SSE citations event."""
    citations: list[dict[str, object]] = []
    for src in sources:
        m = src.chunk.metadata
        citations.append(
            {
                "index": src.index,
                "document_title": m.get("title", ""),
                "source": m.get("source", ""),
                "source_id": m.get("source_id", ""),
                "section_type": m.get("section_type", ""),
                "authors": m.get("authors", []),
                "publication_date": m.get("publication_date", ""),
            },
        )
    return citations
