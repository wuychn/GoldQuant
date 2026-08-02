"""数据源请求统一层：策略模式注入头/限速 + curl_cffi 模拟浏览器 TLS。

为什么 curl_cffi：东财 push2/clist 用 TLS 指纹（JA3）识别 requests/urllib3 为非浏览器并
主动断连（``RemoteDisconnected``）；用 curl_cffi ``impersonate="chrome"`` 模拟 Chrome 的
TLS 握手即可绕过（实测：requests 带 Cookie/UA/Referer 第 1 页就断；curl_cffi 前 7 页成功）。
kline/stockapi 等接口不查 TLS，统一走 curl_cffi 无副作用。

覆盖范围：所有 ``requests.Session.request`` 调用（akshare、东财直连、llm_client 等 requests 系）。
httpx 系（同花顺）头由调用方自建，不在此层。curl_cffi 异常时回退原 requests（自愈）。

新增 requests 系数据源 = 加一个 ``SourceHeaderStrategy`` 子类 + 注册到 ``_STRATEGIES``。
"""

from __future__ import annotations

import json
import random
from abc import ABC
from pathlib import Path
from time import sleep
from typing import Any

import requests
from curl_cffi import requests as cf_requests
from requests.sessions import Session

# common/utils/*.py → 上两级为项目根（含 `.eastmoney.header`）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EASTMONEY_HEADER_FILE = _PROJECT_ROOT / ".eastmoney.header"

_ORIGINAL_SESSION_REQUEST = Session.request
_PATCH_APPLIED = False


# ─── 东财头文件管理（mkt_config endpoint 写入；EastmoneyStrategy 读取）──────────────
def eastmoney_header_file_path() -> Path:
    return EASTMONEY_HEADER_FILE


def load_headers_from_file() -> dict[str, str]:
    """从 `.eastmoney.header` 读取 JSON 数组 [{"key","value"},...] → dict。"""
    if not EASTMONEY_HEADER_FILE.is_file():
        return {}
    try:
        raw = EASTMONEY_HEADER_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, list):
            return {}
        out: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            k = item.get("key")
            v = item.get("value")
            if k is None or v is None:
                continue
            out[str(k)] = str(v)
        return out
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def save_headers_to_file(items: list[dict[str, str]]) -> None:
    """整文件覆盖写入 UTF-8 JSON。"""
    EASTMONEY_HEADER_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ─── 策略模式：按 URL 注入头 + 限速 ────────────────────────────────────────
class SourceHeaderStrategy(ABC):
    """单个数据源的「注入头 + 请求前动作（限速）」策略。"""

    name: str = ""
    domain_patterns: tuple[str, ...] = ()  # URL 命中任一子串即归本策略管

    def matches(self, url: str) -> bool:
        return any(p in url for p in self.domain_patterns)

    def headers(self) -> dict[str, str]:
        """要合并进请求的头（默认空）。"""
        return {}

    def pre_request(self, url: str) -> None:
        """请求前动作，如限速 sleep（默认空）。"""
        return None


class EastmoneyStrategy(SourceHeaderStrategy):
    """东财：注入 `.eastmoney.header`（Cookie/user-agent）+ 轻度限速。

    ``interval_min/max`` 控制每次东财请求前的随机 sleep（秒）；默认 1-3，可用
    ``set_eastmoney_interval`` 运行时调整（如 ``build_daily --req-interval``）。
    """

    name = "eastmoney"
    domain_patterns = ("eastmoney.com",)
    interval_min: int = 1
    interval_max: int = 3

    def headers(self) -> dict[str, str]:
        return load_headers_from_file()

    def pre_request(self, url: str) -> None:
        sleep(random.randint(self.interval_min, self.interval_max))


# 注册表（按序匹配，命中即止）。加新 requests 系数据源在此追加一个策略实例。
_STRATEGIES: list[SourceHeaderStrategy] = [
    EastmoneyStrategy(),
]


def register_source_strategy(strategy: SourceHeaderStrategy) -> None:
    """运行时注册新数据源策略（插件/测试用）。"""
    _STRATEGIES.append(strategy)


def set_eastmoney_interval(min_sec: int, max_sec: int) -> None:
    """运行时调东财请求间隔（秒）：``build_daily --req-interval`` 等用。

    降频（如 3,6）缓解 clist 全市场频控；升频（如 0,1）加速，慎用（易触发限流）。
    """
    if max_sec < min_sec:
        min_sec, max_sec = max_sec, min_sec
    for s in _STRATEGIES:
        if isinstance(s, EastmoneyStrategy):
            s.interval_min, s.interval_max = min_sec, max_sec
            return


def _to_requests_response(cfr: Any, method: str, url: str) -> requests.Response:
    """把 curl_cffi 响应包装成 ``requests.Response``（akshare 等按 requests API 消费）。"""
    r = requests.Response()
    r.status_code = cfr.status_code
    r._content = cfr.content
    r.headers.update(dict(cfr.headers) if cfr.headers is not None else {})
    r.url = str(getattr(cfr, "url", url))
    r.encoding = getattr(cfr, "encoding", None) or "utf-8"
    req = requests.PreparedRequest()
    req.method = method
    req.url = str(url)
    r.request = req
    return r


def _request_proxies() -> dict[str, str | None] | None:
    """行情请求代理：仅当 GOLDQUANT_PROXY_ENABLED 时走应用代理；否则显式禁用系统代理。

    Clash 等注入的 HTTP(S)_PROXY 会导致 push2his.eastmoney.com 出现 ProxyError，
    与「应用未开代理」预期不符，故默认 proxies http/https=None。
    """
    try:
        from common.config import Settings

        settings = Settings()
    except Exception:  # noqa: BLE001
        return {"http": None, "https": None}
    if not settings.PROXY_ENABLED:
        return {"http": None, "https": None}
    px = settings.httpx_proxy_url()
    if not px:
        return {"http": None, "https": None}
    return {"http": px, "https": px}


def _patched_session_request(self: Session, method: str, url: str | bytes, **kwargs: Any) -> Any:
    url_s = url.decode("utf-8", errors="replace") if isinstance(url, bytes) else str(url)
    # 1) 策略：注入头 + 限速（sleep）
    headers = dict(kwargs.get("headers") or {})
    for strat in _STRATEGIES:
        if strat.matches(url_s):
            extra = strat.headers()
            if extra:
                headers.update(extra)
            strat.pre_request(url_s)
            break
    proxies = kwargs.get("proxies")
    if proxies is None:
        proxies = _request_proxies()
    # 2) curl_cffi 发请求（impersonate=chrome 解决东财 clist 的 TLS 指纹反爬）；异常回退原 requests
    try:
        cfr = cf_requests.request(
            method,
            url_s,
            params=kwargs.get("params"),
            headers=headers or None,
            data=kwargs.get("data"),
            json=kwargs.get("json"),
            files=kwargs.get("files"),
            timeout=kwargs.get("timeout") or 15,
            allow_redirects=kwargs.get("allow_redirects", True),
            proxies=proxies,
            impersonate="chrome",
        )
        return _to_requests_response(cfr, method, url_s)
    except Exception:
        # fallback：原 requests（带已注入的头）。覆盖 TLS 不查的接口 + curl_cffi 不兼容场景。
        kwargs["headers"] = headers or None
        kwargs["proxies"] = proxies
        return _ORIGINAL_SESSION_REQUEST(self, method, url, **kwargs)


def apply_source_header_patch() -> None:
    """进程级打补丁：``requests`` 系请求统一走 curl_cffi（impersonate=chrome）+ 策略注入头/限速。

    在 ``quant.data.fetch`` 模块级调用，使 build/update/maintain 脚本与 ``python -m quant`` 直跑
    都自动生效，无需经 app lifespan。curl_cffi 异常自动回退原 requests（自愈）。
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    Session.request = _patched_session_request  # type: ignore[method-assign]
    _PATCH_APPLIED = True
