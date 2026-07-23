"""R2 配置加载。"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

import yaml

from quant.store.paths import config_file, ensure_layout

_PACKAGE_R2_YML = Path(__file__).resolve().parent / "config" / "r2.yml"


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


@lru_cache(maxsize=1)
def load_r2_config() -> dict:
    ensure_layout()
    cfg = copy.deepcopy(_load_yaml(_PACKAGE_R2_YML))
    user = config_file("r2.yml")
    if user.is_file():
        _deep_merge(cfg, _load_yaml(user))
    return cfg


def reload_r2_config() -> None:
    load_r2_config.cache_clear()
