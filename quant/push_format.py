"""消息推送格式化。"""

_PUSH_FOCUS = {
    "news": "关注点：宏观政策、行业热点、外围市场、情绪风向",
    "pre_market": "关注点：集合竞价表现、自选标的信号、持仓风控、当日执行计划",
    "during_market": "关注点：市场状态、买卖信号、持仓监控、仓位风控",
    "post_market_lunch": "关注点：上午行情回顾、自选表现、持仓跟踪、下午策略调整",
    "post_market_evening": "关注点：全天复盘、盈亏总结、自选更新、经验教训",
}


def format_push_message(label: str, timestamp: str, body: str, mode: str) -> str:
    header = f"【{label}】{timestamp}\n"
    parts = [header]
    parts.append(body.strip())
    return "\n".join(parts)
