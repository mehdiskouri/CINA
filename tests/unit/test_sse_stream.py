"""Unit tests for SSE event formatting and keepalive."""

from __future__ import annotations

import asyncio
import json

import pytest

from cina.serving.stream.sse import merge_with_keepalive, sse_event, sse_keepalive


class TestSseEvent:
    def test_format(self) -> None:
        result = sse_event("token", {"text": "hello"})
        assert result.startswith("event: token\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        data_line = result.split("\n")[1]
        parsed = json.loads(data_line.removeprefix("data: "))
        assert parsed == {"text": "hello"}

    def test_metadata_event(self) -> None:
        result = sse_event("metadata", {"query_id": "abc", "cache_hit": False})
        assert "event: metadata\n" in result
        data_line = result.split("\n")[1]
        parsed = json.loads(data_line.removeprefix("data: "))
        assert parsed["query_id"] == "abc"
        assert parsed["cache_hit"] is False

    def test_done_event(self) -> None:
        result = sse_event("done", {})
        assert "event: done\n" in result
        assert '"done"' not in result  # data is {}


class TestSseKeepalive:
    def test_format(self) -> None:
        result = sse_keepalive()
        assert result == ":keepalive\n\n"
        # Must be a comment line (starts with colon) per SSE spec
        assert result.startswith(":")


class TestMergeWithKeepalive:
    @pytest.mark.asyncio
    async def test_yields_all_stream_events(self) -> None:
        events = ["event1", "event2", "event3"]

        async def _stream():  # type: ignore[override]
            for e in events:
                yield e

        collected = []
        async for item in merge_with_keepalive(_stream(), interval_seconds=60):
            collected.append(item)
        assert collected == events

    @pytest.mark.asyncio
    async def test_keepalive_on_slow_stream(self) -> None:
        """Keepalive emitted when stream is slower than the interval."""

        async def _slow_stream():  # type: ignore[override]
            yield "first"
            await asyncio.sleep(0.3)
            yield "second"

        collected = []
        async for item in merge_with_keepalive(_slow_stream(), interval_seconds=0.1):
            collected.append(item)
            if len(collected) > 10:
                break  # safety

        assert "first" in collected
        assert "second" in collected
        # At least one keepalive should have been emitted during the 0.3s wait
        keepalives = [c for c in collected if c == ":keepalive\n\n"]
        assert len(keepalives) >= 1

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        async def _empty():  # type: ignore[override]
            return
            yield  # make it a generator

        collected = []
        async for item in merge_with_keepalive(_empty(), interval_seconds=60):
            collected.append(item)
        assert collected == []
