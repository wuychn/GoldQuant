"""基于「盘中10分钟线」的当日走势过滤（仅 during 接口下发的序列）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


def _to_float(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bar_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """用于将 10 分钟 K 按时间升序排列。"""
    bs = row.get("bucket_start")
    sa = row.get("saved_at")
    for v in (bs, sa):
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        # 形如 20260506T1720
        if len(s) >= 13 and s[8:9] == "T" and s[:8].isdigit():
            try:
                ymd = int(s[:8])
                hm = int(s[9:13]) if len(s) >= 13 and s[9:13].isdigit() else 0
                return (ymd * 10000 + hm, s)
            except ValueError:
                pass
        # 形如 2026-05-06 17:20:00
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return (int(dt.strftime("%Y%m%d%H%M")), s)
        except ValueError:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
                return (int(dt.strftime("%Y%m%d%H%M")), s)
            except ValueError:
                continue
    return (0, "")


def _sorted_10m_bars(stock: dict[str, Any]) -> list[dict[str, Any]]:
    raw = stock.get("盘中10分钟线", [])
    if not isinstance(raw, list):
        return []
    bars = [b for b in raw if isinstance(b, dict)]
    bars.sort(key=_bar_sort_key)
    return bars


class Intraday10mTrendGuardRule(Rule):
    """用当日「盘中10分钟线」序列做弱势过滤；各子项均可独立开关。

    数据来自 ``during_market`` 聚合里 ``股票[].盘中10分钟线``（见 ``load_intraday_10m_bars_tail``）。
    根数不足时可 SKIP，避免无 10m 数据时误杀。
    """

    def default_params(self) -> dict[str, Any]:
        return {
            "enable_min_bar_count": True,
            "min_bar_count": 2,
            # 自当日 10m 序列最高价 max(high) 至最新一根收盘的回撤 ≥ 阈值则视为弱势
            "enable_max_session_drawdown_pct": False,
            "max_session_drawdown_pct": 12.0,
            # 最近 N 根中「收<开」的阴线根数 ≥ min_bearish_bars 则弱势
            "enable_last_n_bars_bearish": False,
            "last_n_bars": 4,
            "min_bearish_bars": 3,
            # 最新收盘相对「首根 K 开盘价」涨幅 < 阈值则弱势（可设负值容忍小幅绿）
            "enable_min_session_gain_from_first_open_pct": False,
            "min_session_gain_from_first_open_pct": -1.5,
            # 最新收盘低于当日各根收盘均价的 ratio 倍则弱势
            "enable_last_vs_mean_close_ratio": False,
            "min_last_vs_mean_close_ratio": 0.998,
        }

    @property
    def name(self) -> str:
        return "盘中10分钟走势"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        code = str(stock.get("股票代码", "")).strip()

        bars = _sorted_10m_bars(stock)
        if not bars:
            return self._skip(f"{name}({code})无盘中10分钟线数据")

        min_need = int(self.params.get("min_bar_count", 2))
        if bool(self.params.get("enable_min_bar_count", True)) and len(bars) < min_need:
            return self._skip(
                f"{name}({code})盘中10分钟线仅{len(bars)}根<{min_need}，不做当日走势判定"
            )

        highs: list[float] = []
        closes: list[float] = []
        opens: list[float] = []
        for b in bars:
            h = _to_float(b.get("high"))
            c = _to_float(b.get("close"))
            o = _to_float(b.get("open"))
            if h is not None:
                highs.append(h)
            if c is not None:
                closes.append(c)
            if o is not None:
                opens.append(o)

        if not closes:
            return self._skip(f"{name}({code})10分钟线无有效收盘价")

        last_close = closes[-1]
        session_high = max(highs) if highs else last_close
        first_open = _to_float(bars[0].get("open"))
        if first_open is None and opens:
            first_open = opens[0]

        if bool(self.params.get("enable_max_session_drawdown_pct", False)):
            cap = float(self.params.get("max_session_drawdown_pct", 12.0))
            if session_high > 0:
                dd = (session_high - last_close) / session_high * 100.0
                if dd >= cap:
                    return self._fail(
                        f"10m序列自高{session_high:.2f}回撤{dd:.2f}%≥{cap:.2f}%，当日走势偏弱"
                    )

        if bool(self.params.get("enable_last_n_bars_bearish", False)):
            n = max(1, int(self.params.get("last_n_bars", 4)))
            need_red = max(1, int(self.params.get("min_bearish_bars", 3)))
            tail = bars[-n:]
            red = 0
            for b in tail:
                o = _to_float(b.get("open"))
                c = _to_float(b.get("close"))
                if o is not None and c is not None and c < o:
                    red += 1
            if red >= need_red:
                return self._fail(
                    f"近{n}根10m中阴线{red}根≥{need_red}，分时走弱"
                )

        if bool(self.params.get("enable_min_session_gain_from_first_open_pct", False)):
            thr = float(self.params.get("min_session_gain_from_first_open_pct", -1.5))
            if first_open is not None and first_open > 0:
                gain = (last_close - first_open) / first_open * 100.0
                if gain < thr:
                    return self._fail(
                        f"自首根10m开盘{first_open:.2f}至最新收盘{last_close:.2f}涨幅{gain:.2f}%<{thr:.2f}%"
                    )

        if bool(self.params.get("enable_last_vs_mean_close_ratio", False)):
            ratio = float(self.params.get("min_last_vs_mean_close_ratio", 0.998))
            if closes:
                mean_c = sum(closes) / len(closes)
                if mean_c > 0 and last_close < mean_c * ratio:
                    return self._fail(
                        f"最新10m收盘{last_close:.2f}<当日10m收盘均值×{ratio:.4f}={mean_c*ratio:.2f}"
                    )

        return self._pass(
            f"{name}({code})10m共{len(bars)}根，当日走势子项未触发或已通过"
        )
