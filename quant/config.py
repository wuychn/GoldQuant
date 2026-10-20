"""quant.yml 加载。包内默认与用户覆盖 deep merge。"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

import yaml

from common.config import get_settings

_PACKAGE_DIR = Path(__file__).resolve().parent
_PACKAGE_QUANT_YML = _PACKAGE_DIR / "config" / "quant.yml"


def get_feishu_config() -> tuple[str, str, str]:
    cfg = get_settings()
    return cfg.FEISHU_APP_ID or "", cfg.FEISHU_APP_SECRET or "", cfg.FEISHU_USER_ID or ""


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
def load_quant_config() -> dict:
    from quant.store.paths import config_file, ensure_layout

    ensure_layout()
    cfg = copy.deepcopy(_load_yaml(_PACKAGE_QUANT_YML))
    user = config_file("quant.yml")
    if user.is_file():
        _deep_merge(cfg, _load_yaml(user))
    _validate_config_quiet(cfg)
    return cfg


def _validate_config_quiet(cfg: dict) -> None:
    from quant.yml_schema import validate_quant_config

    errors = validate_quant_config(cfg)
    if errors:
        raise ValueError(f"quant.yml 校验失败: {'; '.join(errors)}")


@lru_cache(maxsize=1)
def load_gates_config() -> dict:
    return copy.deepcopy(load_quant_config().get("gates") or {})


def reload_config_cache() -> None:
    load_quant_config.cache_clear()
    load_gates_config.cache_clear()

