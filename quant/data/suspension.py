"""停牌建模：行内启发式 + 东财停复牌接口（``stock_tfp_em``）。

- ``is_suspended_from_row(stock)``：无网络，按盘口无报价判定，供买入门禁实时使用。
- ``suspended_codes(date_str)``：按日期拉取 ``stock_tfp_em``，按日期缓存；
  PIT 可回溯，供离线 universe/回测显式剔除停牌股。
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from quant.store.paths import quant_home


def is_suspended_from_row(stock: dict) -> bool:
    """行内启发式：盘口既无最新价也无开盘价 → 视为停牌/无报价。

    买入门禁用此判定，避免对停牌股发买入信号（撮合本就无法成交，
    显式拦截可给出清晰原因，并在漏斗统计中暴露）。

    注意：只看 ``盘口`` 字段，不回退 ``技术指标.last_close``——后者是归档数据，
    会把「盘口为空但有历史指标」的真停牌股误判为在市。

    价格解析走 ``to_float``：盘口值可能是字符串（``"10.5"``）、带千分位或 numpy
    标量，用 ``isinstance(v, (int, float))`` 判断会把在市股误判为停牌，进而在买入
    门禁处全量拦截。
    """
    from quant.scoring.tech_indicators import to_float

    if not isinstance(stock, dict):
        return False
    pk = stock.get("盘口")
    if not isinstance(pk, dict):
        return True  # 无盘口字段视为无报价
    for k in ("最新", "最新价", "今开", "开盘", "开盘价", "open"):
        f = to_float(pk.get(k))
        if f is not None and f > 0:
            return False
    return True


def _tfp_cache_path(date_str: str) -> Path:
    return quant_home() / "cache" / "tfp" / f"{date_str}.json"


@lru_cache(maxsize=64)
def suspended_codes(date_str: str) -> frozenset[str]:
    """某交易日停牌代码集合（``stock_tfp_em``，按日期缓存）。

    ``date_str`` 接受 ``YYYY-MM-DD`` 或 ``YYYYMMDD``。网络失败返回空集。
    """
    ds = date_str.replace("-", "")
    cache = _tfp_cache_path(ds)
    if cache.is_file():
        try:
            arr = json.loads(cache.read_text(encoding="utf-8"))
            return frozenset(str(x).strip() for x in arr if str(x).strip())
        except (json.JSONDecodeError, OSError):
            pass

    try:
        import akshare as ak

        df = ak.stock_tfp_em(date=ds)
    except Exception:
        return frozenset()

    code_col = None
    for c in df.columns:
        if "代码" in str(c):
            code_col = c
            break
    if code_col is None:
        return frozenset()

    codes = {str(v).strip() for v in df[code_col].tolist() if str(v).strip()}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(sorted(codes), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return frozenset(codes)


def is_suspended(code: str, date_str: str) -> bool:
    """按日期查 ``stock_tfp_em`` 是否停牌；网络失败回退 False。"""
    return str(code).strip() in suspended_codes(date_str)


def refresh_suspended(date_str: str) -> int:
    """强制重拉某日停复牌并刷新缓存；返回停牌条数。"""
    ds = date_str.replace("-", "")
    suspended_codes.cache_clear()
    try:
        cache = _tfp_cache_path(ds)
        if cache.is_file():
            cache.unlink()
    except OSError:
        pass
    n = len(suspended_codes(date_str))
    suspended_codes.cache_clear()
    return n
