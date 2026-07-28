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
    try:
        from quant.research.config_schema import validate_quant_config

        errors = validate_quant_config(cfg)
        if errors:
            import logging

            logging.getLogger("quant.config").warning("quant.yml 校验: %s", "; ".join(errors))
    except Exception:
        pass


@lru_cache(maxsize=1)
def load_gates_config() -> dict:
    return copy.deepcopy(load_quant_config().get("gates") or {})


@lru_cache(maxsize=1)
def load_push_config() -> dict:
    """推送展示过滤配置（如自选价格区间）。仅影响推送展示，不影响决策。"""
    return copy.deepcopy(load_quant_config().get("push") or {})


@lru_cache(maxsize=1)
def load_factor_weights(as_of: str | None = None) -> dict[str, float] | None:
    """IC 驱动的因子权重。

    - ``as_of`` 给定：优先读 walk-forward 时变权重表 ``factor_weights_ts.yml``，
      取 ≤as_of 最近一组（严格 OOS，权重只用 t-1 及更早数据拟合）。
    - 否则或时变表缺失：回退静态 ``factor_weights.yml``（周期性拟合，OOS）。
    - 无文件或 ``apply=False`` 返回 None → 调用方回退 ``registry.weights()``。

    返回完整权重表（registry 全因子，未入选因子权重 0），使 ``compose_alpha``
    跳过 IC 阴性/噪声因子。
    """
    from quant.factors.registry import REGISTRY
    from quant.store.paths import config_file

    if as_of:
        ts_path = config_file("factor_weights_ts.yml")
        if ts_path.is_file():
            ts_data = _load_yaml(ts_path)
            if ts_data.get("apply") is not False:
                ts = ts_data.get("weights_ts") or {}
                avail = sorted(d for d in ts if str(d) <= str(as_of))
                if avail:
                    latest = ts[avail[-1]]
                    return {n: float(latest.get(n, 0.0)) for n in REGISTRY.names()}

    path = config_file("factor_weights.yml")
    if not path.is_file():
        return None
    data = _load_yaml(path)
    if data.get("apply") is False:
        return None
    raw = data.get("weights") or {}
    return {n: float(raw.get(n, 0.0)) for n in REGISTRY.names()}


def reload_config_cache() -> None:
    load_quant_config.cache_clear()
    load_gates_config.cache_clear()
    load_push_config.cache_clear()
    load_factor_weights.cache_clear()


def trading_time_checks_enabled() -> bool:
    v = (load_gates_config().get("trading") or {}).get("time_validation_enabled")
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
