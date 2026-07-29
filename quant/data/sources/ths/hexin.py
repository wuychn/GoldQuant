"""同花顺 Hexin-V 与 HTTP 公共常量（自 ths_util 抽离）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import py_mini_racer

HTTP_TIMEOUT = 30
A_SHARE_PREFIXES = ("60", "0", "3")

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'

STOCK_FUNDS_URL = (
    "https://stockpage.10jqka.com.cn/spService/{symbol}/Funds/realFunds/free/1/"
)

_ths_js_ctx: "py_mini_racer.MiniRacer | None" = None


def _get_ths_js_ctx() -> "py_mini_racer.MiniRacer":
    global _ths_js_ctx
    if _ths_js_ctx is None:
        import py_mini_racer
        from akshare.stock_feature.stock_fund_flow import _get_file_content_ths

        _ths_js_ctx = py_mini_racer.MiniRacer()
        _ths_js_ctx.eval(_get_file_content_ths("ths.js"))
    return _ths_js_ctx


def get_v() -> str:
    """生成同花顺 Hexin-V（与 cookie ``v`` 值一致）。"""
    return _get_ths_js_ctx().call("v")


def browser_common_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7,ko;q=0.6",
        "Connection": "keep-alive",
        "Sec-Ch-Ua": SEC_CH_UA,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "x-requested-with": "XMLHttpRequest",
    }
