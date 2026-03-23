from __future__ import annotations

import os
from pathlib import Path

import pytest

from cina.config.loader import clear_config_cache
from cina.ingestion.queue import RedisStreamQueue, SQSQueue, build_queue_backend


def _write_config(path: Path, backend: str) -> None:
    path.write_text(
        "\n".join(
            [
                "ingestion:",
                "  queue:",
                f"    backend: {backend}",
            ],
        )
        + "\n",
        encoding="utf-8",
    )


def test_build_queue_backend_redis_and_sqs(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"

    _write_config(cfg, "redis")
    os.environ["CINA_CONFIG_PATH"] = str(cfg)
    clear_config_cache()
    redis_backend = build_queue_backend()
    assert isinstance(redis_backend, RedisStreamQueue)

    _write_config(cfg, "sqs")
    clear_config_cache()
    sqs_backend = build_queue_backend()
    assert isinstance(sqs_backend, SQSQueue)

    clear_config_cache()
    os.environ.pop("CINA_CONFIG_PATH", None)


def test_build_queue_backend_invalid_backend_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg, "invalid")
    os.environ["CINA_CONFIG_PATH"] = str(cfg)
    clear_config_cache()

    try:
        with pytest.raises(ValueError, match="Unsupported ingestion queue backend"):
            _ = build_queue_backend()
    finally:
        clear_config_cache()
        os.environ.pop("CINA_CONFIG_PATH", None)
