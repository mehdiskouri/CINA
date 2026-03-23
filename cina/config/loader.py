"""Config loading utilities with YAML + environment override merging."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from cina.config.schema import AppConfig, FileConfig


class InvalidYamlRootError(TypeError, ValueError):
    """Raised when the root YAML node is not a mapping."""


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML config file as a dictionary."""
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        message = f"Invalid YAML root for config: {path}"
        raise InvalidYamlRootError(message)
    return loaded


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override values into base dictionary."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_overrides() -> dict[str, Any]:
    """Build nested override structure from `CINA__` environment variables."""
    prefix = "CINA__"
    out: dict[str, Any] = {}
    for env_key, raw in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        path = env_key[len(prefix) :].split("__")
        if not path:
            continue
        cursor = out
        for token in path[:-1]:
            key = token.lower()
            next_value = cursor.get(key)
            if not isinstance(next_value, dict):
                next_value = {}
                cursor[key] = next_value
            cursor = next_value
        cursor[path[-1].lower()] = raw
    return out


@lru_cache(maxsize=1)
def load_config(config_path: str | None = None) -> AppConfig:
    """Load and cache application configuration."""
    path_str = config_path or os.getenv("CINA_CONFIG_PATH") or "cina.yaml"
    path = Path(path_str)
    file_values = FileConfig.model_validate(_load_yaml(path))
    merged = file_values.model_dump(mode="python")
    merged = _deep_merge(merged, _env_overrides())
    return AppConfig(**merged)


def clear_config_cache() -> None:
    """Clear cached configuration object."""
    load_config.cache_clear()
