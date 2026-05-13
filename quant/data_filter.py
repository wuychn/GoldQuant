"""按 lane 过滤 payload，减少 LLM 输入量。"""


_HOT_STOCK_SLIM_KEYS = frozenset({
    "市场", "股票代码", "股票名称", "热度", "涨跌", "人气排名",
    "人气排名变化", "所属概念", "连板情况",
})


def _slim_hot_stock(item: dict) -> dict:
    return {k: v for k, v in item.items() if k in _HOT_STOCK_SLIM_KEYS}


def _trim_history_bars(item: dict, max_bars: int = 5) -> dict:
    out = dict(item)
    hist = out.get("历史行情")
    if isinstance(hist, list) and len(hist) > max_bars:
        out["历史行情"] = hist[-max_bars:]
    return out


def _slim_stock_metadata(item: dict) -> dict:
    keys = {"股票代码", "股票名称", "战法", "加入自选原因", "买入时间", "买入价", "买入原因"}
    return {k: v for k, v in item.items() if k in keys}


def _filter_stocks_by_strategy(items: list, strategy: str) -> list:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("战法", "") or "").strip()
        reason = str(item.get("加入自选原因", "") or item.get("买入原因", "") or "").strip()
        if tag == strategy:
            out.append(item)
        elif not tag or tag == "未标注":
            if strategy == "涨停板战法" and reason.startswith("【涨停板战法】"):
                out.append(item)
            elif strategy == "龙回头战法" and reason.startswith("【龙回头战法】"):
                out.append(item)
            elif strategy == "主升浪战法" and reason.startswith("【主升浪战法】"):
                out.append(item)
    return out


def _hot_stock_for_zt_optional(item: dict) -> dict:
    keep = {"市场", "股票代码", "股票名称", "热度", "涨跌", "人气排名",
            "人气排名变化", "所属概念", "连板情况", "历史行情"}
    out = {k: v for k, v in item.items() if k in keep}
    return _trim_history_bars(out, 5)


def _hot_stock_for_lht_optional(item: dict) -> dict:
    keep = {"市场", "股票代码", "股票名称", "热度", "涨跌", "人气排名",
            "人气排名变化", "所属概念", "连板情况",
            "历史行情", "技术指标", "个股资金流"}
    return {k: v for k, v in item.items() if k in keep}


def _hot_stock_for_zsll_optional(item: dict) -> dict:
    """主升浪加自选与龙回头共用 enrichment 字段。"""
    return _hot_stock_for_lht_optional(item)


def filter_payload(payload: dict, lane: str) -> dict:
    p = payload
    hot = p.get("同花顺人气榜", [])
    zxg = p.get("自选股", [])
    ccg = p.get("持仓股", [])

    if lane == "narrative":
        out = dict(p)
        if hot:
            out["同花顺人气榜"] = [_slim_hot_stock(h) for h in hot]
        return out

    if lane == "zt_optional":
        return {
            "同花顺人气榜": [_hot_stock_for_zt_optional(h) for h in hot[:20]],
            "涨停统计": p.get("涨停统计", []),
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "lht_optional":
        return {
            "同花顺人气榜": [_hot_stock_for_lht_optional(h) for h in hot],
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "zsll_optional":
        return {
            "同花顺人气榜": [_hot_stock_for_zsll_optional(h) for h in hot],
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "overview":
        return {
            "大盘指数": p.get("大盘指数"),
            "赚钱效应": p.get("赚钱效应"),
            "大盘资金流": p.get("大盘资金流"),
            "涨停统计": p.get("涨停统计"),
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "zt_buy":
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "涨停板战法"),
            "同花顺人气榜": [_slim_hot_stock(h) for h in hot[:10]],
            "概念板块": p.get("概念板块", {}),
            "涨停统计": p.get("涨停统计", []),
        }

    if lane == "lht_buy":
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "龙回头战法"),
            "大盘资金流": p.get("大盘资金流"),
        }

    if lane == "zsll_buy":
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "主升浪战法"),
            "大盘资金流": p.get("大盘资金流"),
        }

    if lane == "zt_hold":
        return {
            "持仓股": _filter_stocks_by_strategy(ccg, "涨停板战法"),
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "lht_hold":
        return {
            "持仓股": _filter_stocks_by_strategy(ccg, "龙回头战法"),
        }

    if lane == "zsll_hold":
        return {
            "持仓股": _filter_stocks_by_strategy(ccg, "主升浪战法"),
        }

    if lane == "positions":
        return {
            "持仓股": ccg,
            "自选股": [_slim_stock_metadata(s) for s in zxg],
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "during_narrative":
        # 盘中叙述：大盘 + 市场分析数据 + 全量自选/持仓 + 人气榜前10精简
        return {
            "大盘指数": p.get("大盘指数"),
            "赚钱效应": p.get("赚钱效应"),
            "大盘资金流": p.get("大盘资金流"),
            "涨停统计": p.get("涨停统计"),
            "概念板块": p.get("概念板块"),
            "市场状态机": p.get("市场状态机"),
            "同花顺人气榜": [_slim_hot_stock(h) for h in hot[:10]],
            "自选股": zxg,
            "持仓股": ccg,
        }

    if lane == "pre_market":
        # 盘前单次调用：大盘 + 全量自选/持仓（含盘口/集合竞价/历史/技术指标/资金流）+ 市场状态机
        return {
            "大盘指数": p.get("大盘指数"),
            "自选股": zxg,
            "持仓股": ccg,
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "pre_main":
        return {
            "大盘指数": p.get("大盘指数"),
            "自选股": [_slim_stock_metadata(s) for s in zxg],
            "持仓股": [_slim_stock_metadata(s) for s in ccg],
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "pre_zt":
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "涨停板战法"),
        }

    if lane == "pre_lht":
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "龙回头战法"),
        }

    if lane == "pre_zsll":
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "主升浪战法"),
        }

    return p
