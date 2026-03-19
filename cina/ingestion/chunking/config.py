from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    max_chunk_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"
    respect_section_boundaries: bool = True
    sentence_boundary_alignment: bool = True
