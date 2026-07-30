"""数据层表结构与字段常量。

存储约定
--------
所有日线存不复权 OHLCV，复权因子单独成表，算因子时再乘。
这样 spot_em 的不复权增量可直接追加，除权日不会静默断裂序列；
撮合/涨跌停判定也用真实成交价。

``daily_raw``  : code, date, name, open, high, low, close, pre_close,
                 volume, amount, turnover_rate, float_mv, total_mv
``adj_factor`` : code, date, hfq_factor
``index_daily``: code, date, open, high, low, close, volume, amount
``universe``   : date, code, name, included（逐日 PIT 快照）
``calendar``   : trade_date（YYYY-MM-DD）
"""

from __future__ import annotations

# daily_raw 字段（顺序即 parquet 列顺序）
DAILY_RAW_COLUMNS = (
    "code",
    "date",
    "name",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "float_mv",
    "total_mv",
)

# spot_em 字段名 → daily_raw 字段名
# （pct/vol_ratio/speed 不属 daily_raw，但映射后供盘中因子 SpotRow 使用，write_daily_raw 不会落库这几列）
SPOT_EM_FIELD_MAP = {
    "代码": "code",
    "名称": "name",
    "最新价": "close",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "昨收": "pre_close",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover_rate",
    "流通市值": "float_mv",
    "总市值": "total_mv",
    "涨跌幅": "pct",
    "量比": "vol_ratio",
    "涨速": "speed",
    "主力净流入-净额": "main_net_inflow",
}

# stock_zh_a_hist 字段名 → daily_raw 字段名
HIST_FIELD_MAP = {
    "日期": "date",
    "股票代码": "code",
    "名称": "name",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover_rate",
}

ADJ_FACTOR_COLUMNS = ("code", "date", "hfq_factor")
INDEX_DAILY_COLUMNS = ("code", "date", "open", "high", "low", "close", "volume", "amount")
UNIVERSE_COLUMNS = ("date", "code", "name", "included")
CALENDAR_COLUMNS = ("trade_date",)

# Universe 过滤默认阈值
DEFAULT_MIN_LIST_DAYS = 120
DEFAULT_ADV_LOOKBACK = 20
DEFAULT_MIN_ADV_YI = 0.5  # 5000 万

# ST/退市 名称关键字（PIT：名称随每日快照落库，天然 point-in-time）
ST_NAME_MARKERS = ("ST", "*ST", "退", "PT")
