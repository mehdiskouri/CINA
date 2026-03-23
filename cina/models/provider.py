"""Provider-facing message and completion configuration models."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class Message:
    """Single chat message passed to an LLM provider."""

    role: str
    content: str


@dataclass(slots=True)
class StreamChunk:
    """Incremental text fragment returned during provider streaming."""

    text: str


@dataclass(slots=True)
class CompletionConfig:
    """Request configuration and metadata for text completion."""

    max_tokens: int = 1024
    temperature: float = 0.3
    metadata: dict[str, object] = field(default_factory=dict)
