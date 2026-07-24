"""流动性 / 拥挤度过滤。"""

from __future__ import annotations

from typing import Any

from quant.config import load_quant_config
from quant.scoring.tech_indicators import hist_rows_sorted


def _candidate_universe_cfg() -> dict:
    scoring = load_quant_config().get("scoring") or {}
    return scoring.get("candidate") or {}


def _parse_amount_yi(raw: object) -> float | None:
    """成交额 → 亿元。支持数值（元或亿）与「12.3亿」字符串。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        # ≥100万 视为元；否则视为已是亿元
        if abs(v) >= 1e6:
            return v / 1e8
        return v
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    mult = 1.0
    if s.endswith("万"):
        # 万 → 亿
        mult = 1e-4
        s = s[:-1]
    elif s.endswith("亿"):
        mult = 1.0
        s = s[:-1]
    try:
        v = float(s) * mult
    except ValueError:
        return None
    if abs(v) >= 1e6:
        return v / 1e8
    return v


def avg_daily_amount_yi(stock: dict, *, lookback: int = 20) -> float | None:
    """近 lookback 日平均成交额（亿元）。"""
    amounts: list[float] = []
    for row in hist_rows_sorted(stock.get("历史行情"))[-lookback:]:
        for k in ("成交额", "amount", "额"):
            if row.get(k) is not None:
                yi = _parse_amount_yi(row.get(k))
                if yi is not None and yi > 0:
                    amounts.append(yi)
                break
    # 盘口兜底
    if not amounts:
        pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
        for k in ("成交额", "额"):
            yi = _parse_amount_yi(pk.get(k) or stock.get(k))
            if yi is not None and yi > 0:
                amounts.append(yi)
                break
    if not amounts:
        return None
    return sum(amounts) / len(amounts)


def popularity_rank_of(stock: dict) -> int | None:
    raw = stock.get("人气排名")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def check_liquidity(
    stock: dict,
    *,
    cfg: dict | None = None,
) -> tuple[bool, str]:
    """流动性 + 拥挤硬过滤。缺数据默认放行（避免误杀）。"""
    c = cfg or _candidate_universe_cfg()
    uni = c.get("universe") or {}
    if not uni.get("enabled", True):
        return True, ""

    min_adv = float(uni.get("min_adv_yi", 0) or 0)
    if min_adv > 0:
        adv = avg_daily_amount_yi(stock, lookback=int(uni.get("adv_lookback", 20)))
        if adv is not None and adv < min_adv:
            code = str(stock.get("股票代码", "")).strip()
            return False, f"{code} 日均成交额{adv:.2f}亿<{min_adv}亿"

    # 人气过热：排名 ≤ max_crowding_rank 视为拥挤（1=最热）
    max_crowd = uni.get("max_crowding_rank")
    if max_crowd is not None:
        rank = popularity_rank_of(stock)
        if rank is not None and rank <= int(max_crowd):
            code = str(stock.get("股票代码", "")).strip()
            return False, f"{code} 人气排名{rank}≤{max_crowd}拥挤剔除"

    return True, ""


def filter_universe(rows: list[dict], *, cfg: dict | None = None) -> list[dict]:
    """过滤候选列表；返回通过流动性/拥挤约束的股票。"""
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ok, _ = check_liquidity(row, cfg=cfg)
        if ok:
            out.append(row)
    return out


def participation_notional_cap(
    stock: dict,
    *,
    participation_rate: float = 0.1,
    cfg: dict | None = None,
) -> float | None:
    """单笔名义金额上限 ≈ ADV(元) × participation_rate。"""
    c = cfg or _candidate_universe_cfg()
    uni = c.get("universe") or {}
    lookback = int(uni.get("adv_lookback", 20))
    adv_yi = avg_daily_amount_yi(stock, lookback=lookback)
    if adv_yi is None or adv_yi <= 0:
        return None
    rate = float(uni.get("participation_rate", participation_rate) or participation_rate)
    return adv_yi * 1e8 * max(0.0, rate)


def universe_stats(rows: list[dict]) -> dict[str, Any]:
    """候选池流动性摘要（研究/推送用）。"""
    advs = []
    for r in rows:
        a = avg_daily_amount_yi(r)
        if a is not None:
            advs.append(a)
    if not advs:
        return {"n": len(rows), "adv_median_yi": None, "adv_min_yi": None}
    advs.sort()
    mid = advs[len(advs) // 2]
    return {
        "n": len(rows),
        "n_with_adv": len(advs),
        "adv_median_yi": round(mid, 3),
        "adv_min_yi": round(advs[0], 3),
        "adv_max_yi": round(advs[-1], 3),
    }
