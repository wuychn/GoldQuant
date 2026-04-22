"""
AKShare 及本服务直连同花顺接口的 OpenAPI 入参/出参模型（字段与 AKShare 文档、源码列名一致，便于 /docs 展示）。

`field_desc(模型, "字段名")` 用于在路由的 `Query(..., description=...)` 中复用同一套说明。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ——— 从 Pydantic 入参/出参模型取 description，与 Schema 中表述一致 ———


def field_desc(model: type[BaseModel], name: str) -> str:
    f = model.model_fields.get(name)
    d = f.description if f and f.description else None
    return d or name


# =============================================================================
# 东财：热榜、资讯
# =============================================================================


class _EmptyParams(BaseModel):
    """本接口无查询参数时，响应体 `params` 的占位结构。"""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def as_dict(cls) -> dict[str, Any]:
        return {}


class EmNewsIn(BaseModel):
    """`stock_news_em` 与 Query 共用字段说明。"""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ...,
        description=(
            "名称: `symbol`；类型: str。"
            "股票代码或新闻搜索关键词（如 603777），与 AKShare `stock_news_em` 及东方财富新闻搜索行为一致。"
        ),
    )


class EmHotUpRow(BaseModel):
    """`stock_hot_up_em` 输出行（列名以 AKShare 源码为准）。"""

    model_config = ConfigDict(extra="allow")
    排名较昨日变动: int | float | None = Field(
        default=None, description="排名较上一交易日变动；数值为东财原表。"
    )
    当前排名: int | float | None = Field(default=None, description="当前人气排名。")
    代码: str | None = Field(default=None, description="东财带市场股票代码，如 `SH600000`。")
    股票名称: str | None = Field(default=None, description="股票名称。")
    最新价: float | None = Field(default=None, description="当前最新价。")
    涨跌额: float | None = Field(default=None, description="涨跌额。")
    涨跌幅: float | None = Field(default=None, description="涨跌幅，百分数数值（%）。")


class EmHotRankRow(BaseModel):
    """`stock_hot_rank_em` 输出行。"""

    model_config = ConfigDict(extra="allow")
    当前排名: int | float | None = Field(default=None, description="当前人气排名。")
    代码: str | None = Field(default=None, description="东财带市场股票代码。")
    股票名称: str | None = Field(default=None, description="股票名称。")
    最新价: float | None = Field(default=None, description="当前最新价。")
    涨跌额: float | None = Field(default=None, description="涨跌额。")
    涨跌幅: float | None = Field(default=None, description="涨跌幅，百分数数值（%）。")


class EmNewsRow(BaseModel):
    """`stock_news_em` 行结构（列名以 AKShare 处理后的中文列名为准）。"""

    model_config = ConfigDict(extra="allow")
    关键词: str = Field(..., description="与入参 `symbol` 回显，用于标定本次搜索。")
    新闻标题: str = Field(..., description="文章标题，已去除 <em> 高亮。")
    新闻内容: str = Field(..., description="内容摘要/正文片段。")
    发布时间: str = Field(..., description="东财源数据中的发布时间。")
    文章来源: str = Field(..., description="媒体/来源名。")
    新闻链接: str = Field(..., description="东方财富财讯文章 URL。")


class EmSurgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(..., description="固定形如 `akshare.stock_hot_up_em`。")
    params: dict[str, Any] = Field(
        default_factory=dict, description="本端点无查询入参，固定为空对象。")
    row_count: int = Field(..., description="返回行数。")
    columns: list[str] = Field(
        ..., description="DataFrame 列名顺序，与每行 `rows` 的键一一对应。")
    rows: list[EmHotUpRow] = Field(..., description="飙升榜列表。")


class EmPopularityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: dict[str, Any] = Field(
        default_factory=dict, description="本端点无查询入参，固定为空对象。")
    row_count: int
    columns: list[str]
    rows: list[EmHotRankRow]


class EmNewsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: EmNewsIn = Field(
        ..., description="本次请求入参回显（`symbol` 为查询用关键词或股票代码）。")
    row_count: int
    columns: list[str]
    rows: list[EmNewsRow]


# ——— 东财：个股信息、五档、K 线、分钟、日内 ———


class EmItemValueRow(BaseModel):
    """`stock_individual_info_em` / `stock_bid_ask_em` 均为 item / value 长表。"""

    item: str | int | float | None = Field(
        default=None, description="项名称：个股信息中多为中文指标名；五档中如 sell_1, buy_1, 最新 等。")
    value: str | int | float | None = Field(
        default=None, description="对应项的取值；类型以具体行为准。")

    model_config = ConfigDict(extra="allow")


class EmIndividualInfoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；类型: str。股票代码，如 000001、603777。例: `ak.stock_individual_info_em(symbol=\"000001\")`。")
    timeout: float | None = Field(
        default=None, description="名称: `timeout`；类型: float|None。HTTP 请求超时时间（秒）；不传为库默认，不传则出参中可为 `null`。")


class EmIndividualInfoOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: EmIndividualInfoIn
    row_count: int
    columns: list[str]
    rows: list[EmItemValueRow]


class EmBidAskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；类型: str。股票代码。例: `symbol='000001'`。")


class EmBidAskOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: EmBidAskIn
    row_count: int
    columns: list[str]
    rows: list[EmItemValueRow]


class EmZhAHistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；类型: str。如 603777，可在 `ak.stock_zh_a_spot_em()` 中获取 A 股代码表。")
    period: Literal["daily", "weekly", "monthly"] = Field(
        "daily", description="名称: `period`；类型: str。取值: `daily` 日线 | `weekly` 周线 | `monthly` 月线。")
    start_date: str = Field(
        ..., description="名称: `start_date`；类型: str。开始日期，格式 YYYYMMDD，如 20210301。")
    end_date: str = Field(
        ..., description="名称: `end_date`；类型: str。结束日期，格式 YYYYMMDD，如 20210616。")
    adjust: str = Field(
        "", description="名称: `adjust`；类型: str。默认 `''` 为不复权；`qfq` 前复权；`hfq` 后复权。")
    timeout: float | None = Field(
        default=None, description="名称: `timeout`；类型: float|None。可选 HTTP 超时（秒）。不传为库默认。")


class EmZhAHistRow(BaseModel):
    """`stock_zh_a_hist` 输出-历史行情（日/周/月）。"""

    日期: str | None = Field(default=None, description="交易日期，ISO 或日文字符串。")
    股票代码: str | None = Field(default=None, description="不含市场标识的股票代码。")
    开盘: float | None = Field(default=None, description="开盘价。")
    收盘: float | None = Field(default=None, description="收盘价。")
    最高: float | None = Field(default=None, description="最高价。")
    最低: float | None = Field(default=None, description="最低价。")
    成交量: int | float | None = Field(default=None, description="成交量；单位: 手。")
    成交额: float | None = Field(default=None, description="成交额；单位: 元。")
    振幅: float | None = Field(default=None, description="振幅；单位: %。")
    涨跌幅: float | None = Field(default=None, description="涨跌幅；单位: %。")
    涨跌额: float | None = Field(default=None, description="涨跌额；单位: 元。")
    换手率: float | None = Field(default=None, description="换手率；单位: %。")

    model_config = ConfigDict(extra="allow")


class EmZhAHistOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: EmZhAHistIn
    row_count: int
    columns: list[str]
    rows: list[EmZhAHistRow]


class EmZhAHistMinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；类型: str。股票代码，如 000300。")
    start_date: str = Field(
        ..., description="名称: `start_date`；类型: str。起止为日期时间，如 2024-03-20 09:30:00。默认不填时东财有内部默认，本服务要求传入。")
    end_date: str = Field(
        ..., description="名称: `end_date`；类型: str。如 2024-03-20 15:00:00。")
    period: Literal["1", "5", "15", "30", "60"] = Field(
        "5", description="名称: `period`；分钟周期，1/5/15/30/60；1 分钟仅近端且与复权关系见官方说明。")
    adjust: str = Field(
        "", description="名称: `adjust`；`''` 不复权；`qfq` 前复权；`hfq` 后复权。")


class EmZhAHistMinRow(BaseModel):
    """`stock_zh_a_hist_min_em` 列集随 1 分钟/其它周期不同；此处为并集，均可选。"""

    model_config = ConfigDict(extra="allow")
    时间: str | None = Field(default=None, description="分钟线时间。")
    开盘: float | None = None
    收盘: float | None = None
    最高: float | None = None
    最低: float | None = None
    涨跌幅: float | None = Field(
        default=None, description="1 分钟以外周期通常含涨跌幅、振幅、换手等，单位见文档。")
    涨跌额: float | None = None
    成交量: float | int | None = Field(default=None, description="成交量；手。")
    成交额: float | None = None
    振幅: float | None = None
    换手率: float | None = None
    均价: float | None = Field(default=None, description="1 分钟等周期含均价，其它周期无该列时为空。")


class EmZhAHistMinOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: EmZhAHistMinIn
    row_count: int
    columns: list[str]
    rows: list[EmZhAHistMinRow]


class EmIntradayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；类型: str。股票代码，如 000001。")


class EmIntradayRow(BaseModel):
    时间: str | None = Field(default=None, description="分笔时间，如 09:15:00、14:57:00。")
    成交价: float | None = Field(default=None, description="成交价。")
    手数: int | float | None = Field(default=None, description="手数/成交量，按东财表。")
    买卖盘性质: str | None = Field(
        default=None, description="买卖盘性质。如 买盘/卖盘/中性盘 等，依上游为准。")

    model_config = ConfigDict(extra="allow")


class EmIntradayOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: EmIntradayIn
    row_count: int
    columns: list[str]
    rows: list[EmIntradayRow]


# =============================================================================
# 新浪
# =============================================================================


class SinaZhADailyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；带交易所前缀。如 sh600000、sz000001；可在 `ak.stock_zh_a_spot()` 查看格式。")
    start_date: str = Field(
        ..., description="名称: `start_date`；str，开始 YYYYMMDD。例 19910403。")
    end_date: str = Field(
        ..., description="名称: `end_date`；str，结束 YYYYMMDD。")
    adjust: str = Field(
        "", description="复权: `''` 不复权；`qfq` 前；`hfq` 后；另支持 `qfq-factor` / `hfq-factor` 等，见官方文档。")


class SinaZhADailyRow(BaseModel):
    date: str | None = Field(default=None, description="交易日。")
    open: float | None = Field(default=None, description="开盘价。")
    high: float | None = Field(default=None, description="最高价。")
    low: float | None = Field(default=None, description="最低价。")
    close: float | None = Field(default=None, description="收盘价。")
    volume: float | None = Field(
        default=None, description="成交量。官方说明单位: 股。")
    amount: float | None = Field(
        default=None, description="成交额。官方说明单位: 元。")
    outstanding_share: float | None = Field(
        default=None, description="流动股本；单位: 股。")
    turnover: float | None = Field(
        default=None, description="换手率=成交量/流动股本。")

    model_config = ConfigDict(extra="allow")


class SinaZhADailyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: SinaZhADailyIn
    row_count: int
    columns: list[str]
    rows: list[SinaZhADailyRow]


class SinaMinuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；如 sh600751、sh000300。")
    period: Literal["1", "5", "15", "30", "60"] = Field(
        "1", description="分钟频率 1/5/15/30/60。")
    adjust: str = Field(
        "", description="`''` 不复权；`qfq` 前；`hfq` 后。")


class SinaMinuteRow(BaseModel):
    day: str | None = Field(default=None, description="分钟 K 线时间。官方列名: day。")
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None

    model_config = ConfigDict(extra="allow")


class SinaMinuteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: SinaMinuteIn
    row_count: int
    columns: list[str]
    rows: list[SinaMinuteRow]


class SinaIntradayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(
        ..., description="名称: `symbol`；带市场前缀。如 sz000001。")
    date: str = Field(
        ..., description="名称: `date`；str，交易日期 YYYYMMDD。例 20240321。")


class SinaIntradayRow(BaseModel):
    symbol: str | None = None
    name: str | None = Field(default=None, description="股票名。")
    ticktime: str | None = Field(
        default=None, description="逐笔或分时点时间，如 09:30:00。")
    price: float | None = None
    volume: int | float | None = Field(
        default=None, description="成交量。官方: 股。")
    prev_price: float | None = Field(default=None, description="前一笔或参考价。")
    kind: str | None = Field(
        default=None, description="大单边性质；官方说明: D=卖, U/买盘等因版本略有差异, 以实际为准。")

    model_config = ConfigDict(extra="allow")


class SinaIntradayOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: SinaIntradayIn
    row_count: int
    columns: list[str]
    rows: list[SinaIntradayRow]


# =============================================================================
# 同花顺
# =============================================================================


class ThsIndustryRow(BaseModel):
    """`stock_board_industry_summary_ths` 行（与 AKShare 中文化列名一致，含带连字符列）。"""

    序号: int | float | None = Field(
        default=None, description="名称: 序号；行号。")
    板块: str | None = Field(
        default=None, description="名称: 板块；同花顺行业/板块名。")
    涨跌幅: float | None = Field(
        default=None, description="名称: 涨跌幅；% 。")
    总成交量: float | None = None
    总成交额: float | None = None
    净流入: float | None = None
    上涨家数: int | float | None = None
    下跌家数: int | float | None = None
    均价: float | None = None
    领涨股: str | None = None
    leading_bellwether_price: float | None = Field(
        default=None,
        validation_alias="领涨股-最新价",
        serialization_alias="领涨股-最新价",
        description="名称: `领涨股-最新价`；领涨跌个股最新价。",
    )
    leading_bellwether_change_pct: float | None = Field(
        default=None,
        validation_alias="领涨股-涨跌幅",
        serialization_alias="领涨股-涨跌幅",
        description="名称: `领涨股-涨跌幅`；%。",
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )


class ThsIndustrySummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    params: dict[str, Any] = Field(
        default_factory=dict, description="`stock_board_industry_summary_ths` 无入参，此节为空对象。")
    row_count: int
    columns: list[str]
    rows: list[ThsIndustryRow]


class ThsHotParamsEcho(BaseModel):
    """响应中 `params` 节：同花顺上游 URL 的查询子集本服务回显；`limit` 仅本服务做截取，不写入此对象。"""

    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, protected_namespaces=()
    )
    stock_type: str = Field(
        description="与 Query 中 `stock_type` 相同；市场，如 a。")
    ths_time_grain: str = Field(
        ...,
        validation_alias="type",
        serialization_alias="type",
        description="与上游参数名及 Query 名 `type` 一致。时间粒度，如 hour。",
    )
    list_type: str = Field(..., description="如 normal。")


class ThsHotQueryDoc(BaseModel):
    """同花顺热榜 GET 的查询参数，仅用于 `field_desc` 与 OpenAPI 中名称/类型/含义说明。"""

    model_config = ConfigDict(extra="forbid")
    stock_type: str = Field(
        "a", description="名称: `stock_type`；str。市场，如 a 表示 A 股。默认 a。")
    time_type: str = Field(
        "hour", description="在 URL/Query 中名称为 `type`；str。时间粒度，如 hour。默认 hour。")
    list_type: str = Field(
        "normal", description="名称: `list_type`；如 normal。默认 normal。")
    limit: int | None = Field(
        default=None, ge=1, le=500, description="名称: `limit`；int|None。仅本服务用于截取 `stock_list` 前 N 条，不传不截取。")


class ThsHotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["ths_direct"] = Field(
        default="ths_direct",
        description="固定为 `ths_direct`。",
    )
    url: str = Field(
        ...,
        description="本次请求直连接口 URL 的基址（与 `mkt_heat.HOT_STOCK_LIST_API` 一致），完整查询见 `params` 各键。",
    )
    params: ThsHotParamsEcho = Field(
        ..., description="与上游同花顺请求一致并回显的查询子集（已去掉 `host` 等，仅四至五项键值，见上）。")
    raw: dict[str, Any] = Field(
        ..., description="同花顺返回 JSON 整体；`data.stock_list` 中元素结构依上游。若本服务传递 `limit`，只截取该列表前 N 条到 `raw.data.stock_list`。")
    stock_list_total: int = Field(
        ..., description="在可选截取前，上游 `data.stock_list` 列表长度。")
    stock_list_returned: int = Field(
        ..., description="本次在 `raw` 中 `stock_list` 实际条数；若设 `limit` 可能小于 `stock_list_total`。")
