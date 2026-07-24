"""AKShare 统一封装：板块广度/资金流/个股研报与公告。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
from quant.store.paths import quant_home
from quant.timeutil import cn_date_str

logger = logging.getLogger(__name__)


def _ak():
    try:
        import akshare as ak
    except ImportError:
        return None
    return ak


class AkShareClient:
    """机构研究常用 AKShare 接口集合（带按日磁盘缓存）。"""

    def __init__(self, *, request_delay: float = 0.12) -> None:
        self.request_delay = request_delay

    def _cache_dir(self, category: str, date_str: str) -> Path:
        p = quant_home() / "cache" / "akshare" / category / date_str
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _read_cache(self, path: Path) -> Any | None:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, path: Path, obj: Any) -> None:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def sector_fund_flow_rank(
        self,
        indicator: str = "今日",
        *,
        sector_type: str = "行业资金流",
        date_str: str | None = None,
        use_cache: bool = True,
    ) -> list[dict]:
        ds = date_str or cn_date_str()
        cache = self._cache_dir("sector_fund", ds) / f"{sector_type}_{indicator}.json"
        if use_cache:
            cached = self._read_cache(cache)
            if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
                return cached["rows"]
        ak = _ak()
        if not ak:
            return []
        time.sleep(self.request_delay)
        try:
            df = ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type=sector_type)
        except Exception as exc:
            logger.debug("sector_fund_flow_rank fail: %s", exc)
            return []
        rows = df.to_dict(orient="records") if df is not None and not getattr(df, "empty", True) else []
        self._write_cache(cache, {"date": ds, "indicator": indicator, "sector_type": sector_type, "rows": rows})
        return rows

    def board_constituents_breadth(
        self,
        name: str,
        section: str,
        *,
        date_str: str | None = None,
    ) -> dict[str, Any] | None:
        """成份股涨跌家数 → breadth 快照 dict。"""
        from quant.factors.breadth_cache import fetch_live_breadth, save_cached

        ak = _ak()
        if not ak:
            return None
        ds = date_str or cn_date_str()
        snap = fetch_live_breadth(name, section)
        if not snap:
            return None
        save_cached(ds, section, name, snap)
        return {
            "breadth_pct": snap.breadth_pct,
            "up_count": snap.up_count,
            "down_count": snap.down_count,
            "member_count": snap.member_count,
            "source": snap.source,
        }

    def prefetch_combat_breadth(
        self,
        names: list[tuple[str, str]],
        *,
        date_str: str | None = None,
    ) -> int:
        """批量预取 combat 板块 breadth；names = [(name, section), ...]。"""
        from quant.factors.breadth_cache import prefetch_batch

        ds = date_str or cn_date_str()
        items = [(n, sec, {}) for n, sec in names]
        stats = prefetch_batch(items, date_str=ds, allow_live_ak=True)
        return stats.get("akshare", 0) + stats.get("payload", 0)

    def stock_research_reports(
        self,
        symbol: str,
        *,
        date_str: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """个股研报摘要（东方财富）。"""
        ds = date_str or cn_date_str()
        cache = self._cache_dir("research", ds) / f"{symbol}_report.json"
        cached = self._read_cache(cache)
        if isinstance(cached, list):
            return cached[:limit]
        ak = _ak()
        if not ak:
            return []
        time.sleep(self.request_delay)
        rows: list[dict] = []
        for fn_name in ("stock_research_report_em", "stock_institute_recommend_detail"):
            fn = getattr(ak, fn_name, None)
            if not fn:
                continue
            try:
                df = fn(symbol=symbol) if fn_name == "stock_research_report_em" else fn(symbol=symbol)
            except TypeError:
                try:
                    df = fn(stock=symbol)
                except Exception:
                    continue
            except Exception as exc:
                logger.debug("%s fail %s: %s", fn_name, symbol, exc)
                continue
            if df is not None and not getattr(df, "empty", True):
                rows = df.head(limit).to_dict(orient="records")
                break
        self._write_cache(cache, rows)
        return rows[:limit]

    def stock_news(
        self,
        symbol: str,
        *,
        date_str: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        ds = date_str or cn_date_str()
        cache = self._cache_dir("news", ds) / f"{symbol}_news.json"
        cached = self._read_cache(cache)
        if isinstance(cached, list):
            return cached[:limit]
        ak = _ak()
        if not ak:
            return []
        time.sleep(self.request_delay)
        rows: list[dict] = []
        fn = getattr(ak, "stock_news_em", None)
        if fn:
            try:
                df = fn(symbol=symbol)
                if df is not None and not getattr(df, "empty", True):
                    rows = df.head(limit).to_dict(orient="records")
            except Exception as exc:
                logger.debug("stock_news_em fail %s: %s", symbol, exc)
        self._write_cache(cache, rows)
        return rows[:limit]

    def market_fund_flow_summary(self, *, date_str: str | None = None) -> dict[str, Any]:
        """大盘资金流摘要。"""
        ds = date_str or cn_date_str()
        cache = self._cache_dir("market", ds) / "fund_flow.json"
        cached = self._read_cache(cache)
        if isinstance(cached, dict):
            return cached
        ak = _ak()
        out: dict[str, Any] = {"date": ds}
        if not ak:
            return out
        time.sleep(self.request_delay)
        fn = getattr(ak, "stock_market_fund_flow", None)
        if fn:
            try:
                df = fn()
                if df is not None and not getattr(df, "empty", True):
                    last = df.iloc[-1].to_dict()
                    out["latest"] = {str(k): v for k, v in last.items()}
            except Exception as exc:
                logger.debug("market fund flow fail: %s", exc)
        self._write_cache(cache, out)
        return out

    def section_from_board(self, section: str) -> str:
        return BOARD_INDUSTRY if section == BOARD_INDUSTRY else BOARD_CONCEPT
