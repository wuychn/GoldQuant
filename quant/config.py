"""全局配置：quant.yml 加载与 ML 校准合并。"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings

_PACKAGE_DIR = Path(__file__).resolve().parent
_PACKAGE_QUANT_YML = _PACKAGE_DIR / "config" / "quant.yml"
STRATEGY_FILE = _PACKAGE_DIR / "strategy.md"
BASE_URL = "http://localhost:8085"

LLM_OUTPUT_FORMAT = "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号。\n"
_RE_THINKING = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL)


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


def _apply_ml_scoring(scoring: dict, ml: dict) -> None:
    if ml.get("apply") is False:
        return
    th = ml.get("thresholds") or {}
    for key in ("watchlist_threshold", "buy_threshold", "sell_threshold"):
        if key in th:
            scoring[key] = th[key]
    dw = ml.get("dimension_weights") or {}
    dims = scoring.get("dimensions") or {}
    for name, weight in dw.items():
        if name in dims and isinstance(dims[name], dict):
            dims[name]["weight"] = weight


def _apply_ml_gates(gates: dict, ml: dict) -> None:
    if ml.get("apply") is False:
        return
    conf = ml.get("confirmation")
    if isinstance(conf, dict):
        block = gates.setdefault("confirmation", {})
        for regime in ("强势", "震荡", "弱势"):
            if regime in conf and isinstance(conf[regime], dict):
                block.setdefault(regime, {}).update(conf[regime])
        if "required_count" in conf:
            block["required_count"] = conf["required_count"]
    mw = ml.get("main_wave")
    if isinstance(mw, dict):
        gates.setdefault("main_wave", {}).update(mw)


@lru_cache(maxsize=1)
def load_quant_config() -> dict:
    from quant.store.paths import config_file, ensure_layout

    ensure_layout()
    cfg = copy.deepcopy(_load_yaml(_PACKAGE_QUANT_YML))
    user = config_file("quant.yml")
    if user.is_file():
        _deep_merge(cfg, _load_yaml(user))
    return cfg


@lru_cache(maxsize=1)
def load_scoring_config() -> dict:
    from quant.store.paths import config_file

    scoring = copy.deepcopy(load_quant_config().get("scoring") or {})
    ml = config_file("ml_calibration.yml")
    if ml.is_file():
        _apply_ml_scoring(scoring, _load_yaml(ml))
    return scoring


@lru_cache(maxsize=1)
def load_gates_config() -> dict:
    from quant.store.paths import config_file

    gates = copy.deepcopy(load_quant_config().get("gates") or {})
    ml = config_file("ml_calibration.yml")
    if ml.is_file():
        _apply_ml_gates(gates, _load_yaml(ml))
    return gates


def reload_config_cache() -> None:
    load_quant_config.cache_clear()
    load_scoring_config.cache_clear()
    load_gates_config.cache_clear()


def trading_time_checks_enabled() -> bool:
    v = (load_gates_config().get("trading") or {}).get("time_validation_enabled")
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
