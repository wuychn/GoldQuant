"""openclaw量化数据入口"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from numbers import Integral, Real
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
from fastapi import APIRouter, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.core.config import Settings
from app.schemas.response import Response
from app.utils.common_util import (
    cal_avg,
    get_n_workdays_ago,
    get_val,
    list_to_dict, list_to_dict_v2, _normalize_quant_datetime_string, _should_normalize_datetime_like_string,
    _yyyymmdd_to_iso,
)
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import hsgtzj, pk, ztgc, hist, all_stocks, jbxx, ztgc_with_date
from app.utils.etf52_util import zdfb_52etf
from app.utils.quant_archive import (
    load_computed_metrics_zh,
    quant_archive_base, daily_hist_fetch_start_date,
)
from app.utils.quant_market_enrich import (
    pre_auction_minute_zh,
)
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, zdfb_ths, ggzjl

logger = logging.getLogger(__name__)

router = APIRouter(tags=["量化入口"])

_INDEX_SERIAL_WHITELIST = (1, 2, 4)
# 自选/持仓落盘：~/.quant/ 下 JSONL（一行一条 JSON 对象）
QUANT_OPTIONAL_FILENAME = "optional.jsonl"
QUANT_HOLDING_FILENAME = "holding.jsonl"

_SH_TZ = ZoneInfo("Asia/Shanghai")


def _sync_call_or_none(context: str, fn: Callable[[], object]) -> object | None:
    try:
        return fn()
    except Exception:
        _log_api_error(context)
        return None


def _normalize_quant_datetimes(obj: Any) -> Any:
    """将疑似日期时间的字符串规范为 ``yyyy-MM-dd HH:mm:ss`` 或 ``yyyy-MM-dd``。"""
    if isinstance(obj, dict):
        return {k: _normalize_quant_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_quant_datetimes(v) for v in obj]
    if isinstance(obj, str) and _should_normalize_datetime_like_string(obj):
        return _normalize_quant_datetime_string(obj)
    return obj


def _parse_price_scalar(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _spot_price_from_pk_for_10m(pk: dict[str, Any] | None) -> float | None:
    """从东财 ``pk(list_to_dict)`` 中解析现价，供 10 分钟线落盘。"""
    if not isinstance(pk, dict) or not pk:
        return None
    for key in ("最新价", "最新", "现价", "成交价", "最新成交价"):
        if key in pk:
            p = _parse_price_scalar(pk[key])
            if p is not None and p > 0:
                return p
    b1 = _parse_price_scalar(pk.get("买一")) or _parse_price_scalar(pk.get("买1"))
    s1 = _parse_price_scalar(pk.get("卖一")) or _parse_price_scalar(pk.get("卖1"))
    if b1 is not None and s1 is not None and b1 > 0 and s1 > 0:
        return round((b1 + s1) / 2.0, 4)
    if b1 is not None and b1 > 0:
        return b1
    if s1 is not None and s1 > 0:
        return s1
    return None


def _bars_10m_dir(settings: Settings) -> Path:
    d = quant_archive_base(settings) / "bars_10m"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _intraday_10m_last_bucket_key(jl_path: Path) -> str | None:
    if not jl_path.is_file():
        return None
    try:
        lines = jl_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        b = o.get("bucket_start")
        if isinstance(b, str):
            return b
    return None


def _row_date_yyyymmdd(row: dict, *, date_key: str = "日期") -> str | None:
    v = row.get(date_key)
    if v is None:
        return None
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y%m%d")
        except Exception:
            pass
    s = str(v).strip().replace("-", "").replace("/", "")[:8]
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return None


def _rows_last_n_trade_days(
        rows: list,
        *,
        n: int,
        date_key: str = "日期",
) -> list:
    """锚日为行中最大 ``date_key``；保留 [第 n-1 个交易日, 锚日] 闭区间（含锚日共至多 n 个交易日）。"""
    if not isinstance(rows, list) or not rows or n <= 0:
        return []
    dated: list[tuple[str, dict]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _row_date_yyyymmdd(r, date_key=date_key)
        if d:
            dated.append((d, r))
    if not dated:
        return list(rows[-n:]) if len(rows) >= n else list(rows)
    dated.sort(key=lambda x: x[0])
    anchor = dated[-1][0]
    iso = _yyyymmdd_to_iso(anchor)
    if not iso:
        return [r for _, r in dated[-n:]]
    oldest = get_n_workdays_ago(iso, n - 1)
    if oldest is None:
        return [r for _, r in dated[-n:]]
    out = [r for d, r in dated if oldest <= d <= anchor]
    return out


def _round_floats_for_api(obj: Any, *, ndigits: int = 2) -> Any:
    """递归将浮点数四舍五入到 ``ndigits`` 位；整数、布尔、字符串等保持原样。"""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, Integral) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, Real) and not isinstance(obj, bool):
        return round(float(obj), ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats_for_api(v, ndigits=ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats_for_api(v, ndigits=ndigits) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_round_floats_for_api(v, ndigits=ndigits) for v in obj)
    return obj


def _finalize_quant_payload(obj: Any) -> Any:
    """深拷贝 → 日期时间规范化 → 浮点舍入（用于接口统一出参）。"""
    try:
        cloned = copy.deepcopy(obj)
    except Exception:
        cloned = obj
    return _round_floats_for_api(_normalize_quant_datetimes(cloned))


def _slim_xw_list(xw: object, *, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(xw, list):
        return []
    out: list[Any] = []
    for row in xw[:limit]:
        if not isinstance(row, dict):
            continue
        slim = [row[k] for k in ["新闻内容"] if k in row]
        if slim:
            out.append(slim)
    return out


# 规则引擎「强势」硬门槛依赖这些键，须优先保留（避免 list(zqxy) 截断时丢失）
_EARNING_EFFECT_PRIORITY_KEYS: tuple[str, ...] = (
    "上涨",
    "下跌",
    "涨停",
    "跌停",
    "真实涨停",
    "真实跌停",
    "st st*涨停",
    "st st*跌停",
    "平盘",
    "停牌",
    "活跃度",
    "统计日期",
)


def _slim_earning_effect_dict(zqxy: object, *, max_pairs: int = 22) -> dict[str, Any] | None:
    if not isinstance(zqxy, dict) or not zqxy:
        return None
    out: dict[str, Any] = {}
    for k in _EARNING_EFFECT_PRIORITY_KEYS:
        if k in zqxy:
            out[k] = zqxy[k]
    for k, v in zqxy.items():
        if k in out:
            continue
        out[k] = v
        if len(out) >= max_pairs:
            break
    return out


def _merge_concept_boards(jzf: list | None, jzj: list | None, *, limit: int = 10) -> dict[str, Any]:
    return {
        "涨幅榜": (jzf or [])[:limit],
        "资金流入榜": (jzj or [])[:limit],
    }


def _log_api_error(context: str) -> None:
    """记录上游/本地调用失败，带固定上下文便于定位，避免单点异常拖垮整次聚合。"""
    logger.exception("量化数据接口异常 [%s]", context)


def _jbxx(symbol):
    try:
        return jbxx(symbol)
    except Exception:
        _log_api_error(f"股票基本信息 | ak.stock_individual_info_em")
        return None


async def _ggzjl(symbol):
    try:
        # ak.stock_fund_flow_individual() 这个是获取所有
        # 下面是获取指定个股的资金，也是有hexin-v这个header
        # 页面 https://stockpage.10jqka.com.cn/002580/funds/#funds_sszjlx
        # 接口 https://stockpage.10jqka.com.cn/spService/002580/Funds/realFunds/free/1/
        # 本接口还可以获取到行业资金流入、行业涨跌幅、行业资金流入/流出靠前个股
        r = await ggzjl(symbol)
        flash_ = r['flash']
        v_ = list_to_dict_v2(flash_, 'name', 'sr')
        v_['大单流出'] = str(v_['大单流出']) + ' 万元',
        v_['中单流出'] = str(v_['中单流出']) + ' 万元',
        v_['小单流出'] = str(v_['小单流出']) + ' 万元',
        v_['小单流入'] = str(v_['小单流入']) + ' 万元',
        v_['中单流入'] = str(v_['中单流入']) + ' 万元',
        v_['大单流入'] = str(v_['大单流入']) + ' 万元',
        v_['总流入'] = r['title']['zlr'] + ' 万元',
        v_['总流出'] = r['title']['zlc'] + ' 万元'
        v_['净额'] = r['title']['je'] + ' 万元'
        return v_
    except Exception:
        _log_api_error(f"个股资金流 symbol={symbol!r}")
        return None


def _pk(symbol):
    try:
        return pk(symbol)
    except Exception:
        _log_api_error(f"盘口 | ak.stock_bid_ask_em")
        return None


async def _dataframe_records_or_none(context: str, fetch: Callable[..., object]) -> list | None:
    try:
        return dataframe_to_records(await run_in_threadpool(fetch))
    except Exception:
        _log_api_error(context)
        return None


async def _market_fund_flow_last_n(context: str, n: int) -> list | None:
    """
    大盘资金流：``ak.stock_market_fund_flow`` 取最近 ``n`` 条（复盘接口用 3；盘中/盘前用 1）。
    经过测试，盘中获取到的可能都是上一个交易日的数据，需要确认盘后能否获取到当天的数据 TODO
    """
    try:
        recs = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))
        if not recs:
            return []
        if n <= 0:
            return []
        return recs[-n:] if len(recs) >= n else recs
    except Exception:
        _log_api_error(context)
        return None


async def _earning_effect_pre_market(context: str) -> dict | None:
    try:
        return list_to_dict(
            dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu))
        )
    except Exception:
        _log_api_error(context)
        return None


async def _earning_effect_intraday(context: str) -> dict | None:
    try:
        return list_to_dict(
            dataframe_to_records(await run_in_threadpool(lambda: ak.stock_market_activity_legu()))
        )
    except Exception:
        _log_api_error(context)
        return None


async def _stock_fund_flow_concept_or_none(context: str, rank_by: str):
    try:
        return await stock_fund_flow_concept("即时", rank_by)
    except Exception:
        _log_api_error(f"{context} rank_by={rank_by!r}")
        return None


async def _all_stocks_or_none(context: str):
    try:
        return await all_stocks()
    except Exception:
        _log_api_error(context)
        return None


async def _ztgc_or_none(context: str):
    try:
        return ztgc()
    except Exception:
        _log_api_error(context)
        return None


async def _hsgtzj_or_none(context: str):
    try:
        return await run_in_threadpool(hsgtzj)
    except Exception:
        _log_api_error(context)
        return None


def _hist(settings, symbol):
    # 历史行情 TODO
    if settings.QUANT_ARCHIVE_ENABLED:
        start_d = daily_hist_fetch_start_date(settings, symbol)
        hist_api = _sync_call_or_none(
            f"{list_context} dfcf.hist symbol={symbol!r}",
            lambda: hist(symbol, period="daily", start_date=start_d),
        )
        if not isinstance(hist_api, list):
            hist_api = []
        hist_ = load_merge_write_daily_bars(settings, symbol, hist_api)
    else:
        def _hist_no_archive() -> object:
            start = get_n_workdays_ago(None, 60)
            if start:
                return hist(symbol, start_date=start)
            return hist(symbol)

        hist_ = _sync_call_or_none(
            f"{list_context} dfcf.hist symbol={symbol!r}",
            _hist_no_archive,
        )
    if not hist_:
        hist_ = []
    hist_out = _rows_last_n_trade_days(hist_, n=30)
    return hist_out


async def _enrich_stock_list(
        settings: SettingsDep,
        fetch_stocks: Callable[..., Awaitable[list]],
        *,
        more: bool,
        include_pre_snapshot: bool = False,
        fund_flow_trade_days: int = 3,
) -> list:
    out: list = []
    try:
        rows = await fetch_stocks(settings=settings)
        for item in rows:
            try:
                symbol = item["股票代码"]
            except Exception:
                _log_api_error(f"获取股票代码")
                continue

            # 基本信息
            jbxx_ = _jbxx(symbol)
            if jbxx_:
                item['总股本'] = jbxx_['总股本']
                item['流通股'] = jbxx_['流通股']
                item['总市值'] = jbxx_['总市值']
                item['流通市值'] = jbxx_['流通市值']
                item['上市时间'] = jbxx_['上市时间']

            # 盘口
            pk_raw = _pk(symbol)
            item["盘口"] = pk_raw if isinstance(pk_raw, dict) else {}

            # 历史行情，优化代码，不需要配置，默认就保存，优先从本地获取，盘中需要根据当前价格计算 TODO
            # macd等指标需要历史K线，但是历史K线可能错误，直接找其他接口代替吧 TODO
            hist_ = _hist(settings, symbol)
            item["历史行情"] = hist_

            # 五日线 TODO 这些指标在 load_computed_metrics_zh里面会进行计算，这里就不需要了
            avg_5: float | None = None
            hist_5 = hist_[-5:]
            if hist_5 and len(hist_5) >= 5:
                avg_5 = cal_avg(hist_5, "收盘")

            # 十日线
            avg_10: float | None = None
            hist_10 = hist_[-10:]
            if hist_10 and len(hist_10) >= 10:
                avg_10 = cal_avg(hist_10, "收盘")

            # 20日线
            avg_20: float | None = None
            hist_20 = hist_[-20:]
            if hist_20 and len(hist_20) >= 20:
                avg_20 = cal_avg(hist_20, "收盘")

            # 30日线
            avg_30: float | None = None
            if hist_ and len(hist_) >= 30:
                avg_30 = cal_avg(hist_, "收盘")

            tzh: dict[str, Any] | None = None
            if settings.QUANT_ARCHIVE_ENABLED:
                tzh = load_computed_metrics_zh(settings, symbol)
                if tzh:
                    item["技术指标"] = tzh

            # 简化代码 TODO
            if not (isinstance(tzh, dict) and tzh.get("均线5日") is not None) and avg_5 is not None:
                item["5日线"] = avg_5
            if not (isinstance(tzh, dict) and tzh.get("均线10日") is not None) and avg_10 is not None:
                item["10日线"] = avg_10
            if not (isinstance(tzh, dict) and tzh.get("均线20日") is not None) and avg_20 is not None:
                item["20日线"] = avg_20
            if not (isinstance(tzh, dict) and tzh.get("均线30日") is not None) and avg_30 is not None:
                item["30日线"] = avg_30

            # 使用机器学习模型根据分钟行情进行判断 TODO
            if include_pre_snapshot:
                pm = pre_auction_minute_zh(f"{route} | 分钟行情", symbol)
                if pm is None:
                    item["分钟行情"] = []
                elif isinstance(pm, list):
                    item["分钟行情"] = pm
                else:
                    item["分钟行情"] = []

            # 个股资金流 TODO 是否是实时数据？
            zj_raw = await _ggzjl(symbol)
            item["个股资金流"] = zj_raw

            # 振幅，换手 TODO

            if more:
                # 对于规则引擎，新闻数据没有价值，后续可以通过LLM评分后给规则引擎（TODO）
                # xw_ = _sync_call_or_none(
                #     f"{list_context} dfcf.xw symbol={symbol!r}",
                #     lambda: xw(symbol),
                # )
                # item["个股新闻"] = _slim_xw_list(xw_)

                pass

            out.append(item)
    except Exception:
        _log_api_error(f"{route}.fetch_list")
        out = []
    return out


def _quant_data_file(name: str) -> Path:
    return Path.home() / ".quant" / name


def _parse_jsonl_stock_text(text: str) -> list:
    """JSONL：每行一条 JSON 对象；``#`` 开头行为注释；单行若为数组则展开其中对象。"""
    raw: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict):
                    raw.append(it)
        elif isinstance(obj, dict):
            raw.append(obj)
    return _normalize_quant_stock_rows(raw)


def _normalize_quant_stock_rows(raw: list | None) -> list:
    if not raw:
        return []
    out: list = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = item.get("股票代码")
        if code is None or str(code).strip() == "":
            continue
        row = dict(item)
        row["股票代码"] = str(code).strip()
        out.append(row)
    return out


def _zt_height(pool: list[dict[str, Any]] | None):
    """
    计算市场连扳高度
    :param pool:
    :return:
    """
    if not pool:
        return None
    mx = 0
    for r in pool:
        v = r.get("连板数")
        try:
            if v is not None and v != "":
                mx = max(mx, int(float(v)))
        except (TypeError, ValueError):
            continue
    return mx


def _load_stock_rows_from_quant_file(filename: str) -> list:
    """读取 ``~/.quant/{filename}``，仅支持 JSONL（一行一条 JSON）。"""
    path = _quant_data_file(filename)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _log_api_error(f"quant read file path={path!s}")
        return []
    return _parse_jsonl_stock_text(text)


_NOT_TRADING_DAY_RESPONSE = Response(code=1, message="今天不是交易日", data=None)


async def _zx(settings):
    """从 ``~/.quant/optional.jsonl`` 读取自选股（一行一条 JSON）。"""
    return await run_in_threadpool(lambda: _load_stock_rows_from_quant_file(QUANT_OPTIONAL_FILENAME))


async def _cc(settings):
    """从 ``~/.quant/holding.jsonl`` 读取持仓（一行一条 JSON）。"""
    return await run_in_threadpool(lambda: _load_stock_rows_from_quant_file(QUANT_HOLDING_FILENAME))


def _combine_cls_publish_datetime(pub_date_val: object, pub_time_val: object) -> str:
    """财联社 ``发布日期`` + ``发布时间`` → ``yyyy-MM-dd HH:mm:ss``。"""
    ds = str(pub_date_val or "").strip()
    ts = str(pub_time_val or "").strip()
    day = ""
    if "T" in ds:
        day = ds.split("T")[0].replace("/", "-")[:10]
    elif re.match(r"^\d{4}-\d{2}-\d{2}", ds):
        day = ds[:10]
    if not day or len(day) < 10:
        day = datetime.now(_SH_TZ).strftime("%Y-%m-%d")
    ts = ts.replace("：", ":").strip()
    if not ts:
        return f"{day} 00:00:00"
    parts = [p for p in ts.split(":") if p != ""]
    try:
        if len(parts) >= 3:
            return f"{day} {int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        if len(parts) == 2:
            return f"{day} {int(parts[0]):02d}:{int(parts[1]):02d}:00"
    except (ValueError, IndexError):
        pass
    return f"{day} {ts}"


@router.get(
    "/quant/market/news",
    response_model=Response,
    summary="新闻",
    description="新闻",
)
async def news(settings: SettingsDep) -> Response:
    """
    全球/同花顺/财联社资讯聚合到 ``news`` 列表后，**每次请求**调用
    ``refresh_news_market_summary_sync``：使用全局 LLM（``.env`` 中 ``LLM_API_KEY`` /
    ``LLM_BASE_URL`` / ``LLM_MODEL``，或兼容 ``GOLDQUANT_LLM_*``）生成 **500 字以内** 当日影响摘要，
    成功则 **覆盖** 写入 ``~/.quant/news_market_impact_summary.txt``；
    未配置密钥或 LLM 失败则不覆盖该文件。
    """

    news: list = []

    # 全球财经资讯
    try:
        dfcf_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_em))
        if dfcf_data:
            for d in dfcf_data:
                if get_val(d, "标题") or get_val(d, "摘要"):
                    row = {
                        "标题": get_val(d, "标题"),
                        "摘要": get_val(d, "摘要"),
                        "来源": "东方财富",
                    }
                    p = get_val(d, "发布时间")
                    if p not in (None, ""):
                        row["发布时间"] = str(p).strip()
                    news.append(row)
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_em")

    # 同花顺财经
    try:
        ths_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_ths))
        if ths_data:
            for d in ths_data:
                if get_val(d, "标题") or get_val(d, "内容"):
                    pt = get_val(d, "发布时间")
                    news.append(
                        {
                            "标题": get_val(d, "标题"),
                            "摘要": get_val(d, "内容"),
                            "发布时间": str(pt).strip() if pt not in (None, "") else "",
                            "来源": "同花顺",
                        }
                    )
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_ths")

    # 财联社电报
    try:
        cls_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_cls))
        if cls_data:
            for d in cls_data:
                if get_val(d, "标题", "") or get_val(d, "摘要", ""):
                    news.append(
                        {
                            "标题": get_val(d, "标题"),
                            "摘要": get_val(d, "摘要"),
                            "发布时间": _combine_cls_publish_datetime(
                                get_val(d, "发布日期"),
                                get_val(d, "发布时间"),
                            ),
                            "来源": "财联社",
                        }
                    )
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_cls")

    return Response(data=_finalize_quant_payload(news))


async def _dpzs():
    """
    获取大盘指数
    :param route:
    :return:
    """
    try:
        # 东方财富渠道
        raw = dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))
        )
        return [item for item in raw if item["序号"] in _INDEX_SERIAL_WHITELIST]
    except Exception:
        _log_api_error(f"大盘指数 | ak.stock_zh_index_spot_em")
        return None


async def _zqxy():
    """
    涨跌分布
    :return:
    """
    try:
        # 52etf渠道
        return await zdfb_52etf()
    except:
        _log_api_error(f"赚钱效应 | 52etf涨跌分布")

    try:
        # 同花顺渠道
        return await zdfb_ths()
    except Exception:
        _log_api_error(f"赚钱效应 | 同花顺涨跌分布")

    # legu渠道
    # 开盘时获取到的是昨天的数据，且现在执行在报错，就暂时不添加
    # zqxy = await _earning_effect_pre_market(f"赚钱效应 | ak.stock_market_activity_legu")
    return None


async def _ztgk(more: bool = False):
    result = {}
    try:
        zt_full = await run_in_threadpool(lambda: ztgc())
        height = _zt_height(zt_full)
        result['今日涨停'] = zt_full
        result['市场高度'] = str(height) + '连扳'
    except:
        _log_api_error(f"今日涨停股全量 | ak.stock_zt_pool_em")

    if more:
        try:
            # 昨日涨停，复盘时保存当日涨停，优先从保存的读，没有再调用这个接口 TODO
            zrzt = ztgc_with_date(get_n_workdays_ago(n=1))
            result['昨日涨停'] = zrzt
        except:
            _log_api_error(f"昨日涨停股池全量 | ak.stock_zt_pool_em")

    return result


@router.get(
    "/quant/market/pre_market",
    response_model=Response,
    summary="盘前",
    description="盘前",
)
async def pre_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    盘前
    """
    # 大盘指数
    dpzs_ = await _dpzs()

    # 赚钱效应
    zqxy_ = await _zqxy()

    # 涨停概况
    ztgk_ = await _ztgk()

    # 自选，从 ~/.quant/optional.jsonl 获取（每行 {"股票代码","股票名称",...}）
    zxg_ = await _enrich_stock_list(
        settings,
        _zx,
        more=False,
        include_pre_snapshot=True,
        fund_flow_trade_days=1,
    )

    # 持仓，从 ~/.quant/holding.jsonl 获取持仓股（每行一条 JSON，含 股票代码、买入时间 等）
    ccg_ = await _enrich_stock_list(
        route,
        settings,
        _cc,
        more=False,
        include_pre_snapshot=True,
        fund_flow_trade_days=1,
    )

    result = {
        "大盘指数": dpzs_,
        '赚钱效应': zqxy_,
        '涨停概况': ztgk_,
        "自选股": zxg_,
        "持仓股": ccg_
    }

    return Response(data=_finalize_quant_payload(result))


@router.get(
    "/quant/market/during_market",
    response_model=Response,
    summary="盘中",
    description="盘中",
)
async def during_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    盘中
    """
    route = "GET /quant/market/during_market"
    dpzs = await _dpzs(f"{route}")

    # TODO 数据延迟太大，可能是昨天的数据
    # zqxy_raw = await _earning_effect_intraday(f"{route} | ak.stock_market_activity_legu()")
    # zqxy = _slim_earning_effect_dict(zqxy_raw)
    # zjl = await _market_fund_flow_last_n(f"{route} | ak.stock_market_fund_flow", 1)

    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "流入资金",
    )
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn)
    zttj = await _ztgc_or_none(f"{route} | dfcf.ztgc")
    try:
        raw_hot = await hot_stock(settings)
        thsrqg = raw_hot[:20] if isinstance(raw_hot, list) else []
    except Exception:
        _log_api_error(f"{route} | ths.hot_stock (no enrich)")
        thsrqg = []

    # 从 ~/.quant/optional.jsonl 获取自选股；全量盘口；每次 during_market 调用追加一根 10 分钟 K 并返回「盘中10分钟线」
    zxg = await _enrich_stock_list(
        settings,
        _zx,
        more=False,
        list_context=f"{route} | _zx",
        record_and_attach_10m_bars=True,
        fund_flow_trade_days=1,
    )

    # 从 ~/.quant/holding.jsonl 获取持仓股（同上）
    ccg = await _enrich_stock_list(
        settings,
        _cc,
        more=False,
        list_context=f"{route} | _cc",
        record_and_attach_10m_bars=True,
        fund_flow_trade_days=1,
    )

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "同花顺人气榜": thsrqg,
        "自选股": zxg,
        "持仓股": ccg,
        **bundle,
    }
    return Response(data=_finalize_quant_payload(result))


@router.get(
    "/quant/market/post_market",
    response_model=Response,
    summary="盘后",
    description="盘后",
)
async def post_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    盘后：结构同盘中为主增「同花顺人气榜」约 50 条；「大盘资金流」「个股资金流」均为 **最近 3 个交易日**。
    """
    # if (blocked := await _guard_real_workday_or_non_trading_response()) is not None:
    #     return blocked
    route = "GET /quant/market/post_market"
    dpzs = await _dpzs(f"{route} | ak.stock_zh_index_spot_em")
    zqxy_raw = await _earning_effect_intraday(f"{route} | ak.stock_market_activity_legu()")
    zqxy = _slim_earning_effect_dict(zqxy_raw)
    zjl = await _market_fund_flow_last_n(f"{route} | ak.stock_market_fund_flow", 3)
    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "流入资金",
    )
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn)
    zttj = await _ztgc_or_none(f"{route} | dfcf.ztgc")
    thsrqg = await _enrich_stock_list(
        settings,
        hot_stock,
        more=True,
        list_context=f"{route} | ths.hot_stock",
        hot_limit=10,
        fund_flow_trade_days=3,
    )

    # 从 ~/.quant/optional.jsonl 获取自选股（每行 {"股票代码","股票名称",...}）
    zxg = await _enrich_stock_list(
        settings,
        _zx,
        more=False,
        list_context=f"{route} | _zx",
        fund_flow_trade_days=3,
    )

    # 从 ~/.quant/holding.jsonl 获取持仓股（每行一条 JSON，含 股票代码、买入时间 等）
    ccg = await _enrich_stock_list(
        settings,
        _cc,
        more=False,
        list_context=f"{route} | _cc",
        fund_flow_trade_days=3,
    )

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "同花顺人气榜": thsrqg,
        "自选股": zxg,
        "持仓股": ccg,
    }
    return Response(data=_finalize_quant_payload(result))
