"""数据层表结构与字段常量。

存储约定
--------
所有日线存不复权 OHLCV，复权因子单独成表，算因子时再乘。
这样 spot_em 的不复权增量可直接追加，除权日不会静默断裂序列；
撮合/涨跌停判定也用真实成交价。

量纲（全库统一，源差异在入库时对齐）：
- ``volume``：股（东财 hist/spot 原始为手，×100；新浪 hist 已是股；新浪 spot 已 ×100）
- ``amount`` / ``float_mv`` / ``total_mv``：元
- ``turnover_rate``：百分比
- ``name``：不灌当前名到历史行（ST 前视）；靠 ``name_snapshot`` PIT
- ``pre_close``：缺则用同码上一日 ``close``

``daily_raw``  : code, date, name, open, high, low, close, pre_close,
                 volume, amount, turnover_rate, float_mv, total_mv
``adj_factor`` : code, date, hfq_factor
``index_daily``: code, date, open, high, low, close, volume, amount
``lhb``        : code, date, name, reason, net_amt, buy_amt, sell_amt, …（无上榜后收益）
``zt_pool``    : code, date, name, pct, close, boards, first_seal, break_n, …（东财涨停池截面）
``zt_zb`` / ``zt_dt`` / ``zt_prev`` : 炸板 / 跌停 / 昨日涨停今日表现（窗口短，按日覆盖）
``auction``    : date, code, name, kind, time, info（竞价异动，仅当日快照）
``hot_rank``   : code, rank, hot_rank_z（仅当日采集，公开源无历史日期）
``industry_day``: 当日行业强度（由 spot+行业映射汇总，不打网）
``concept_board``: 当日概念板块涨跌幅（theme_mom 顺带落）
``lhb_jg`` / ``lhb_yyb`` / ``lhb_seat`` : 机构买卖、活跃营业部、个股席位明细
``lhb_seat_event``: 席位事件 + 自算前向收益（T+1 缺口 / T+k 收盘，无源站前瞻列）
``lhb_seat_stats``: 按席位+方向的 PIT 胜率/均值（只用不晚于 as_of 的标签）
``minute_5m``  : 事件票 5 分钟 K（约一个月可补）
``minute_1m``  : 事件票当日 1 分钟（通常不能补历史）
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
