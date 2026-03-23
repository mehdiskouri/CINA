from __future__ import annotations

from pathlib import Path

import pytest

from cina.config.loader import (
    _deep_merge,
    _env_overrides,
    _load_yaml,
    clear_config_cache,
    load_config,
)


def test_load_yaml_handles_missing_file(tmp_path: Path) -> None:
    out = _load_yaml(tmp_path / "missing.yaml")
    assert out == {}


def test_load_yaml_rejects_non_mapping_root(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid YAML root"):
        _load_yaml(cfg)


def test_deep_merge_prefers_override_and_recurses() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    override = {"a": {"y": 3}, "c": 4}
    out = _deep_merge(base, override)

    assert out == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}


def test_env_overrides_parses_nested_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CINA__SERVING__SEARCH__VECTOR_TOP_K", "25")
    monkeypatch.setenv("CINA__OBSERVABILITY__LOG_LEVEL", "DEBUG")

    overrides = _env_overrides()

    assert overrides["serving"]["search"]["vector_top_k"] == "25"
    assert overrides["observability"]["log_level"] == "DEBUG"


def test_load_config_merges_yaml_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_config_cache()
    cfg = tmp_path / "cina.test.yaml"
    cfg.write_text(
        "serving:\n  search:\n    vector_top_k: 15\nobservability:\n  log_level: INFO\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CINA__SERVING__SEARCH__VECTOR_TOP_K", "33")

    loaded = load_config(str(cfg))

    assert loaded.serving.search.vector_top_k == 33
    assert loaded.observability.log_level == "INFO"

    clear_config_cache()
