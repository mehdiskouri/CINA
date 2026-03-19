from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from cina.config.schema import AppConfig, FileConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid YAML root for config: {path}")
    return loaded


@lru_cache(maxsize=1)
def load_config(config_path: str | None = None) -> AppConfig:
    path_str = config_path or os.getenv("CINA_CONFIG_PATH") or "cina.yaml"
    path = Path(path_str)
    file_values = FileConfig.model_validate(_load_yaml(path))
    merged = file_values.model_dump(mode="python")
    return AppConfig(**merged)


def clear_config_cache() -> None:
    load_config.cache_clear()
