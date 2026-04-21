"""进程级 HTTP(S) 代理：写入标准环境变量，供 requests / curl_cffi（AKShare）等使用。"""

from __future__ import annotations

import os

from app.core.config import Settings


def apply_process_proxy(settings: Settings) -> None:
    """在进程内设置 HTTP_PROXY / HTTPS_PROXY / NO_PROXY（仅当开启应用代理时）。"""
    if not settings.PROXY_ENABLED:
        return
    http = settings.proxy_http_effective()
    https = settings.proxy_https_effective()
    if http:
        os.environ["HTTP_PROXY"] = http
    if https:
        os.environ["HTTPS_PROXY"] = https
    np = settings.PROXY_NO_PROXY.strip() if settings.PROXY_NO_PROXY else ""
    if np:
        os.environ["NO_PROXY"] = np
