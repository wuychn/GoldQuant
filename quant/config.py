"""全局配置：quant.yml 加载与 ML 校准合并。"""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import yaml

from app.core.config import get_settings

_PACKAGE_DIR = Path(__file__).resolve().parent
_PACKAGE_QUANT_YML = _PACKAGE_DIR / "config" / "quant.yml"
STRATEGY_FILE = _PACKAGE_DIR / "strategy.md"
BASE_URL = "http://localhost:8085"

LLM_OUTPUT_FORMAT = (
    "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号；"
    "禁止出现「程序结论」「程序确认」「规则引擎」「全局门禁」「门禁」「标的池」"
    "「研判要点」「研判中的」「接口数据」「JSON 字段」"
    "「市场环境」「市场档位」「市场状态」「交易环境」「可参与交易」等系统或内部用语；"
    "行情强弱用「赚钱效应强/一般/差」，仓位用「仓位控制」及具体比例；"
    "概念与榜单用「当日涨幅」「资金流入」等自然说法。\n"
)
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
        for key in (
            "default_persistence_minutes",
            "default_min_consecutive_runs",
            "max_window_minutes",
        ):
            if key in conf:
                block[key] = conf[key]
    mw = ml.get("main_wave")
    if isinstance(mw, dict):
        gates.setdefault("main_wave", {}).update(mw)
    ct = ml.get("concept_tracker")
    if isinstance(ct, dict):
        block = gates.setdefault("concept_tracker", {})
        sw = ct.get("score_weights")
        if isinstance(sw, dict):
            score_weights = block.setdefault("score_weights", {})
            score_weights.update(sw)


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


@lru_cache(maxsize=1)
def load_push_config() -> dict:
    """推送展示过滤配置（如自选价格区间）。仅影响推送展示，不影响决策。"""
    return copy.deepcopy(load_quant_config().get("push") or {})


_STRATEGY_KEYS = ("regime", "sector", "pool", "entry", "exit", "ml", "backtest", "portfolio", "risk", "research")

_runtime_r2_override: dict | None = None


@lru_cache(maxsize=1)
def _load_r2_config_base() -> dict:
    """策略决策配置（regime/sector/双池/exit/ml/backtest），来自 quant.yml。"""
    from quant.store.paths import config_file, ensure_layout

    ensure_layout()
    cfg = load_quant_config()
    out = {k: copy.deepcopy(cfg.get(k) or {}) for k in _STRATEGY_KEYS}
    legacy = config_file("r2.yml")
    if legacy.is_file():
        _deep_merge(out, _load_yaml(legacy))
    return out


def load_r2_config() -> dict:
    if _runtime_r2_override is not None:
        return _runtime_r2_override
    return _load_r2_config_base()


@contextmanager
def override_r2_config(cfg: dict) -> Iterator[None]:
    """回测/验证时注入临时 R2 配置（覆盖所有 `from quant.config import load_r2_config` 引用）。"""
    global _runtime_r2_override
    prev = _runtime_r2_override
    _runtime_r2_override = cfg
    try:
        yield
    finally:
        _runtime_r2_override = prev


def reload_config_cache() -> None:
    load_quant_config.cache_clear()
    load_scoring_config.cache_clear()
    load_gates_config.cache_clear()
    load_push_config.cache_clear()
    _load_r2_config_base.cache_clear()


def trading_time_checks_enabled() -> bool:
    v = (load_gates_config().get("trading") or {}).get("time_validation_enabled")
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
