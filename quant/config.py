"""全局配置：路径、YAML 合并策略、飞书/LLM 辅助常量。

配置合并说明
------------
scoring（评分）:
  quant/config/scoring.yml
    → ~/.quant/config/scoring.yml
    → ~/.quant/config/ml_calibration.yml（ML 校准，apply: true）

gates（硬门禁）:
  quant/config/gates.yml
    → ~/.quant/config/gates.yml

修改 YAML 后若进程已启动，需重启 quant；ML --apply 后会调用 reload_config_cache()。
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings

_PACKAGE_DIR = Path(__file__).resolve().parent
STRATEGY_FILE = _PACKAGE_DIR / "strategy.md"
# 量化机器人拉取 FastAPI 聚合数据的基址（须与 app 监听端口一致）
BASE_URL = "http://localhost:8085"

LLM_OUTPUT_FORMAT = "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号。\n"
_RE_THINKING = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL)


def get_feishu_config() -> tuple[str, str, str]:
    """从 .env 读取飞书 tenant 凭据与接收人 open_id。"""
    cfg = get_settings()
    return cfg.FEISHU_APP_ID or "", cfg.FEISHU_APP_SECRET or "", cfg.FEISHU_USER_ID or ""


def _load_yaml(path: Path) -> dict:
    """安全读取 YAML；文件不存在或解析失败时返回空 dict。"""
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _deep_merge(dst: dict, src: dict) -> None:
    """递归合并 src 到 dst（用户配置覆盖默认值，不整表替换）。"""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def _apply_ml_overrides(base: dict, ml: dict) -> None:
    """将 ML 校准结果写入内存中的 scoring 配置（不修改包内源文件）。"""
    if ml.get("apply") is False:
        return
    th = ml.get("thresholds") or {}
    for key in ("watchlist_threshold", "buy_threshold", "sell_threshold"):
        if key in th:
            base[key] = th[key]
    dw = ml.get("dimension_weights") or {}
    dims = base.get("dimensions") or {}
    for name, weight in dw.items():
        if name in dims and isinstance(dims[name], dict):
            dims[name]["weight"] = weight


@lru_cache(maxsize=4)
def load_scoring_config() -> dict:
    """加载评分配置（带缓存；校准后需 reload_config_cache）。"""
    from quant.store.paths import config_file, ensure_layout

    ensure_layout()
    base = copy.deepcopy(_load_yaml(_PACKAGE_DIR / "config" / "scoring.yml"))
    user_path = config_file("scoring.yml")
    if user_path.is_file():
        _deep_merge(base, _load_yaml(user_path))
    ml_path = config_file("ml_calibration.yml")
    if ml_path.is_file():
        _apply_ml_overrides(base, _load_yaml(ml_path))
    return base


@lru_cache(maxsize=4)
def load_gates_config() -> dict:
    """加载硬门禁配置（带缓存）。"""
    from quant.store.paths import config_file, ensure_layout

    ensure_layout()
    base = copy.deepcopy(_load_yaml(_PACKAGE_DIR / "config" / "gates.yml"))
    user_path = config_file("gates.yml")
    if user_path.is_file():
        _deep_merge(base, _load_yaml(user_path))
    return base


def reload_config_cache() -> None:
    """ML --apply 或手动改 YAML 后调用，使下次 load_* 重新读盘。"""
    load_scoring_config.cache_clear()
    load_gates_config.cache_clear()


def trading_time_checks_enabled() -> bool:
    """是否限制为真实交易日 + 连续竞价时段才允许模拟成交。"""
    cfg = load_gates_config()
    trading = cfg.get("trading") or {}
    v = trading.get("time_validation_enabled")
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
