"""组合暴露约束：概念/行业集中度（按现价市值）。"""

from __future__ import annotations

from quant.config import load_quant_config
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import quote_last_price


def _portfolio_cfg() -> dict:
    return load_quant_config().get("portfolio") or {}


def concept_of_stock(stock: dict, payload: dict) -> str:
    """取个股首要概念（简化）。"""
    concepts = stock.get("概念") or stock.get("所属概念") or []
    if isinstance(concepts, list) and concepts:
        return str(concepts[0])
    if isinstance(concepts, str) and concepts:
        return concepts.split(",")[0].strip()
    for row in payload.get("自选股") or []:
        if str(row.get("股票代码", "")).strip() == str(stock.get("股票代码", "")).strip():
            c = row.get("概念") or row.get("所属概念")
            if isinstance(c, list) and c:
                return str(c[0])
            if isinstance(c, str) and c:
                return c.split(",")[0].strip()
    return ""


def industry_of_stock(stock: dict, payload: dict) -> str:
    """取个股行业。"""
    for key in ("行业", "所属行业", "板块"):
        val = stock.get(key)
        if isinstance(val, list) and val:
            return str(val[0]).strip()
        if isinstance(val, str) and val.strip():
            return val.split(",")[0].strip()
    code = str(stock.get("股票代码", "")).strip()
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if str(row.get("股票代码", "")).strip() != code:
                continue
            for ik in ("行业", "所属行业", "板块"):
                val = row.get(ik)
                if isinstance(val, list) and val:
                    return str(val[0]).strip()
                if isinstance(val, str) and val.strip():
                    return val.split(",")[0].strip()
    return ""


def _holding_mark_value(h: dict) -> float:
    """持仓名义：优先现价，否则买入价。"""
    qty = int(h.get("持仓股数", 0) or 0)
    if qty <= 0:
        return 0.0
    px = quote_last_price(h)
    if px is None or px <= 0:
        try:
            px = float(h.get("买入价", 0) or 0)
        except (TypeError, ValueError):
            px = 0.0
    return float(px) * qty


def check_concentration_constraints(
    stock: dict,
    ctx: ScoreContext,
    *,
    holdings: list[dict],
    proposed_value: float,
    total_assets: float,
) -> tuple[bool, str]:
    """买入前检查概念/行业集中度（现价市值）。"""
    cfg = _portfolio_cfg().get("constraints") or {}
    if not cfg.get("enabled", True):
        return True, ""
    if total_assets <= 0:
        return True, ""

    max_concept_pct = float(cfg.get("max_concept_pct", 40))
    max_industry_pct = float(cfg.get("max_industry_pct", 40))

    concept = concept_of_stock(stock, ctx.payload)
    if concept:
        concept_mv = proposed_value
        for h in holdings:
            if concept_of_stock(h, ctx.payload) == concept:
                concept_mv += _holding_mark_value(h)
        if concept_mv / total_assets * 100 > max_concept_pct:
            return False, f"概念{concept}暴露超{max_concept_pct:.0f}%"

    industry = industry_of_stock(stock, ctx.payload)
    if industry:
        industry_mv = proposed_value
        for h in holdings:
            if industry_of_stock(h, ctx.payload) == industry:
                industry_mv += _holding_mark_value(h)
        if industry_mv / total_assets * 100 > max_industry_pct:
            return False, f"行业{industry}暴露超{max_industry_pct:.0f}%"

    return True, ""
