"""Unit tests for prompt construction."""

from uuid import uuid4

from cina.models.search import SearchResult
from cina.serving.context.assembler import NumberedSource
from cina.serving.context.prompt import CLINICAL_SYSTEM_PROMPT, build_messages


def _source(index: int, content: str = "sample content", **meta: object) -> NumberedSource:
    defaults: dict[str, object] = {
        "source": "pubmed",
        "title": "Test Article",
        "section_type": "results",
    }
    defaults.update(meta)
    return NumberedSource(
        index=index,
        chunk=SearchResult(
            chunk_id=uuid4(),
            content=content,
            token_count=10,
            metadata=defaults,
            score=0.9,
        ),
    )


class TestBuildMessages:
    def test_returns_system_and_user_messages(self) -> None:
        sources = [_source(1)]
        messages = build_messages("What is metformin?", sources)
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"

    def test_system_prompt_default(self) -> None:
        messages = build_messages("test", [_source(1)])
        assert messages[0].content == CLINICAL_SYSTEM_PROMPT

    def test_system_prompt_override(self) -> None:
        messages = build_messages("test", [_source(1)], system_prompt="Custom prompt")
        assert messages[0].content == "Custom prompt"

    def test_source_numbering_in_user_message(self) -> None:
        sources = [_source(1, content="First"), _source(2, content="Second")]
        messages = build_messages("query", sources)
        user = messages[1].content
        assert "[Source 1]" in user
        assert "[Source 2]" in user
        assert "First" in user
        assert "Second" in user

    def test_source_metadata_in_user_message(self) -> None:
        sources = [_source(1, source="fda", title="Drug Label", section_type="dosage")]
        messages = build_messages("query", sources)
        user = messages[1].content
        assert "fda" in user
        assert "Drug Label" in user
        assert "dosage" in user

    def test_query_appears_in_user_message(self) -> None:
        messages = build_messages("What are contraindications?", [_source(1)])
        assert "What are contraindications?" in messages[1].content

    def test_empty_sources_still_valid(self) -> None:
        messages = build_messages("query", [])
        assert len(messages) == 2
        assert "Question: query" in messages[1].content

    def test_citation_instructions_present(self) -> None:
        messages = build_messages("query", [_source(1)])
        user = messages[1].content
        assert "[1]" in user or "Cite sources" in user
