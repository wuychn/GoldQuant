"""机构式风控门禁（模拟盘）。"""

from __future__ import annotations

from quant.config import load_r2_config
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import quote_last_price
from quant.store.state import compute_holdings_market_value, get_cash, get_holdings, get_total_assets


class RiskManager:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg if cfg is not None else (load_r2_config().get("risk") or {})

    def check_buy(
        self,
        code: str,
        *,
        sector_tags: list[str],
        price: float,
        quantity: int,
        ctx: ScoreContext,
    ) -> tuple[bool, str]:
        if not self.cfg.get("enabled", True):
            return True, ""
        if price <= 0 or quantity < 100:
            return False, "无效价格或股数"

        holdings = get_holdings()
        total = get_total_assets()
        if total <= 0:
            return True, ""

        cash = get_cash()
        cost = price * quantity * 1.001
        if cost > cash + 1e-6:
            return False, "可用资金不足"

        mv = compute_holdings_market_value(holdings)
        new_mv = mv + price * quantity
        max_deploy = float(self.cfg.get("max_deploy_pct", 0.95))
        if new_mv / total > max_deploy:
            return False, f"总仓位超{max_deploy*100:.0f}%"

        max_single = float(self.cfg.get("max_single_name_pct", 0.20))
        name_mv = price * quantity
        for h in holdings:
            if str(h.get("股票代码", "")).strip() == code:
                px = quote_last_price(h) or float(h.get("买入价") or 0)
                name_mv += px * int(h.get("持仓股数", 0) or 0)
        if name_mv / total > max_single:
            return False, f"单票暴露超{max_single*100:.0f}%"

        max_sector = float(self.cfg.get("max_sector_pct", 0.35))
        if sector_tags and max_sector < 1.0:
            sector = sector_tags[0]
            sector_mv = 0.0
            for h in holdings:
                tags = h.get("sector_tags") or []
                if sector in tags or sector in str(h.get("买入原因", "")):
                    px = quote_last_price(h) or float(h.get("买入价") or 0)
                    sector_mv += px * int(h.get("持仓股数", 0) or 0)
            if code not in {str(x.get("股票代码", "")).strip() for x in holdings}:
                sector_mv += price * quantity
            if sector_mv / total > max_sector:
                return False, f"板块{sector}暴露超{max_sector*100:.0f}%"

        peak = float(self.cfg.get("equity_peak") or total)
        if total < peak:
            dd = (peak - total) / peak * 100
            halt = float(self.cfg.get("max_drawdown_halt_pct", 15))
            if dd >= halt:
                return False, f"回撤{dd:.1f}%触发熔断"
        return True, ""
