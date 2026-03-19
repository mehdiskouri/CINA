from dataclasses import dataclass


@dataclass(slots=True)
class Message:
    role: str
    content: str


@dataclass(slots=True)
class StreamChunk:
    text: str


@dataclass(slots=True)
class CompletionConfig:
    max_tokens: int = 1024
    temperature: float = 0.3
