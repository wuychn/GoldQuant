"""统一飞书推送入口。"""

from __future__ import annotations

import logging

from quant.config import get_feishu_config
from quant.push.feishu import get_token, send_msg
from quant.push.format import format_push_message
from quant.timeutil import cn_datetime_str

logger = logging.getLogger(__name__)

MODE_LABELS = {
    "news": "财经新闻",
    "pre_market": "盘前策略",
    "during_market": "智能盯盘",
    "post_market_lunch": "午间复盘",
    "post_market_evening": "晚间复盘",
}


def push_enabled() -> bool:
    app_id, secret, user_id = get_feishu_config()
    return bool(app_id and secret and user_id)


def push_message(label: str, body: str, *, timestamp: str | None = None) -> bool:
    """推送飞书；未配置时仅打印日志。"""
    ts = timestamp or cn_datetime_str()
    text = format_push_message(label, ts, body)
    if not push_enabled():
        logger.warning("飞书未配置，跳过推送 [%s] len=%s", label, len(body))
        print(text[:2000])
        return False
    try:
        token = get_token()
        send_msg(text, token)
        return True
    except Exception as exc:
        logger.error("飞书推送失败 [%s]: %s", label, exc)
        return False
