"""Prompt construction — builds the LLM messages array from assembled context."""

from __future__ import annotations

from cina.models.provider import Message
from cina.serving.context.assembler import NumberedSource

CLINICAL_SYSTEM_PROMPT = (
    "You are a clinical evidence assistant. You synthesize information from "
    "peer-reviewed medical literature, FDA drug labels, and clinical trial records "
    "to answer clinical questions accurately. Always cite your sources using "
    "bracket notation [1], [2], etc. If the provided sources do not contain "
    "sufficient information to answer the question, state that explicitly. "
    "Never fabricate medical information."
)


def build_messages(
    query: str,
    sources: list[NumberedSource],
    system_prompt: str | None = None,
) -> list[Message]:
    """Build the messages array for LLM completion.

    Uses numbered sources with metadata for citation tracking.
    """
    prompt = system_prompt or CLINICAL_SYSTEM_PROMPT

    context_block = "\n\n".join(
        f"[Source {s.index}] ({s.chunk.metadata.get('source', 'unknown')}: "
        f"{s.chunk.metadata.get('title', 'Untitled')}, "
        f"Section: {s.chunk.metadata.get('section_type', 'general')})\n"
        f"{s.chunk.content}"
        for s in sources
    )

    user_content = (
        "Answer the following clinical question using ONLY the provided sources. "
        "Cite sources using [1], [2], etc. If the sources do not contain sufficient "
        "information, say so explicitly.\n\n"
        f"Sources:\n{context_block}\n\n"
        f"Question: {query}"
    )

    return [
        Message(role="system", content=prompt),
        Message(role="user", content=user_content),
    ]
