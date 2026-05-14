"""规则上下文：携带规则评估所需的全部市场数据。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quant.data_io import compute_holdings_market_value


@dataclass
class RuleContext:
    """规则链评估的数据载体。

    各字段从 orchestrator 中构建后传入规则链。
    未提供的字段保持默认空值，规则内部自行判断数据可用性。
    """

    # 市场状态机原始数据
    market_state: dict[str, Any] = field(default_factory=dict)

    # 大盘指数（含上证/深证/创业板）
    index_data: dict[str, Any] = field(default_factory=dict)

    # 大盘资金流（可能含1日或3日）
    capital_flow: list[dict[str, Any]] = field(default_factory=list)

    # 涨停统计列表
    limit_up_stats: list[dict[str, Any]] = field(default_factory=list)

    # 同花顺人气榜
    popularity_list: list[dict[str, Any]] = field(default_factory=list)

    # 概念板块（涨幅榜 + 资金流入榜）
    concept_sectors: dict[str, Any] = field(default_factory=dict)

    # 自选股列表
    watchlist: list[dict[str, Any]] = field(default_factory=list)

    # 持仓股列表
    holdings: list[dict[str, Any]] = field(default_factory=list)

    # 总权益（现金 + 持仓市值，元）；与 data_io.get_fund() 一致
    fund: float = 0.0

    # 近期止损记录 [{股票代码, 股票名称, 止损时间, 原因}]
    stoploss_records: list[dict[str, Any]] = field(default_factory=list)

    # 当前评估的目标股票（用于对单只股票执行规则链）
    target_stock: dict[str, Any] = field(default_factory=dict)

    # 赚钱效应数据
    profit_effect: dict[str, Any] = field(default_factory=dict)

    # 当日已实现盈亏合计（元）：来自当日 trades_buy/sell 汇总，非浮动盈亏
    daily_pnl: float = 0.0

    # 附加数据（规则间传递中间结果）
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def get_index(self, name: str = "上证指数") -> dict[str, Any]:
        """获取指定指数数据。"""
        if isinstance(self.index_data, list):
            for item in self.index_data:
                if item.get("指数名称") == name or item.get("名称") == name:
                    return item
            return {}
        return self.index_data if self.index_data.get("指数名称") == name else {}

    def get_shangzheng_change(self) -> float | None:
        """获取上证指数涨跌幅。"""
        idx = self.get_index("上证指数")
        if not idx:
            # 尝试从 market_state 获取
            realtime = self.market_state.get("今日大盘实时", {})
            v = realtime.get("涨跌幅")
            if v is not None:
                return float(v)
            return None
        v = idx.get("涨跌幅", idx.get("涨幅"))
        return float(v) if v is not None else None

    def get_market_state_field(self, *keys: str) -> Any:
        """从 market_state 中按层级取值。"""
        obj: Any = self.market_state
        for k in keys:
            if isinstance(obj, dict):
                obj = obj.get(k)
            else:
                return None
        return obj

    def get_yesterday_zt_avg_change(self) -> float | None:
        """昨日涨停表现涨跌幅均值。"""
        v = self.get_market_state_field("昨日涨停表现", "涨跌幅均值")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        # 兼容 "均值" 字段
        v = self.get_market_state_field("昨日涨停表现", "均值")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return None

    def get_max_consecutive_boards(self) -> int | None:
        """今日最高连板数。"""
        v = self.get_market_state_field("今日涨停统计", "市场最高连板数")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        # fallback: 从 limit_up_stats 计算
        if self.limit_up_stats:
            boards = [int(s.get("连板数", 0) or 0) for s in self.limit_up_stats]
            return max(boards) if boards else None
        return None

    def get_limit_up_count(self) -> int | None:
        """今日涨停家数。"""
        v = self.get_market_state_field("今日涨停统计", "涨停家数")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        if self.limit_up_stats:
            return len(self.limit_up_stats)
        return None

    def get_volume_ratio(self) -> float | None:
        """两市成交额相对近5日均倍率。"""
        v = self.get_market_state_field("两市成交额近似", "今日相对近5日均倍率")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return None

    def get_sz_vs_ma20(self) -> float | None:
        """上证指数收盘较20日均线（百分比）。"""
        v = self.get_market_state_field("上证指数", "收盘较20日均线")
        if v is not None:
            try:
                # 可能是 "1.5%" 或 1.5
                s = str(v).replace("%", "").strip()
                return float(s)
            except (TypeError, ValueError):
                return None
        return None

    def _profit_effect_int(self, key: str) -> int | None:
        """从 JSON「赚钱效应」读取整型字段（接口常为 float）。"""
        pe = self.profit_effect
        if not isinstance(pe, dict):
            return None
        v = pe.get(key)
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def get_profit_effect_advancers(self) -> int | None:
        """赚钱效应：上涨家数。"""
        return self._profit_effect_int("上涨")

    def get_profit_effect_decliners(self) -> int | None:
        """赚钱效应：下跌家数。"""
        return self._profit_effect_int("下跌")

    def get_profit_effect_limit_up(self) -> int | None:
        """赚钱效应：涨停家数（字段「涨停」）。"""
        return self._profit_effect_int("涨停")

    def get_profit_effect_limit_down(self) -> int | None:
        """赚钱效应：跌停家数（字段「跌停」）。"""
        return self._profit_effect_int("跌停")

    def find_in_limit_up(self, code: str) -> dict[str, Any] | None:
        """在涨停统计中查找某股票。"""
        code_clean = code.strip()
        for item in self.limit_up_stats:
            if str(item.get("代码", "")).strip() == code_clean:
                return item
            if str(item.get("股票代码", "")).strip() == code_clean:
                return item
        return None

    def find_in_popularity(self, code: str) -> dict[str, Any] | None:
        """在人气榜中查找某股票。"""
        code_clean = code.strip()
        for item in self.popularity_list:
            if str(item.get("股票代码", "")).strip() == code_clean:
                return item
        return None

    def get_concept_top_industries(self) -> set[str]:
        """获取概念板块涨幅榜+资金流入榜前十行业集合。"""
        industries: set[str] = set()
        for key in ("涨幅榜", "资金流入榜"):
            items = self.concept_sectors.get(key, [])
            if isinstance(items, list):
                for item in items:
                    name = item.get("行业", "")
                    if name:
                        industries.add(name)
        return industries

    def is_in_stoploss_cooling(self, code: str, cooling_days: int = 3) -> bool:
        """检查某股票是否处于止损冷却期。"""
        from datetime import datetime, timedelta

        code_clean = code.strip()
        now = datetime.now()
        for rec in self.stoploss_records:
            if str(rec.get("股票代码", "")).strip() != code_clean:
                continue
            time_str = str(rec.get("止损时间", "") or rec.get("卖出时间", "") or "")
            if not time_str:
                continue
            try:
                dt = datetime.strptime(time_str[:10], "%Y-%m-%d")
                if (now - dt).days <= cooling_days:
                    return True
            except ValueError:
                continue
        return False

    def current_position_ratio(self) -> float:
        """当前持仓市值 / 总权益（0~1）。"""
        if self.fund <= 0:
            return 0.0
        if self.extra.get("position_amount") is not None:
            return float(self.extra["position_amount"]) / self.fund
        mv = compute_holdings_market_value(self.holdings)
        return mv / self.fund
