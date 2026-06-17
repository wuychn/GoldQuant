"""精简错误日志：不打印完整堆栈，输出错误类型、原因、报错位置，ERROR 级红色。"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

RED = "\033[31m"
RESET = "\033[0m"
_ANSI_READY = False


def _enable_console_ansi() -> None:
    global _ANSI_READY
    if _ANSI_READY:
        return
    _ANSI_READY = True
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def color_red(text: str) -> str:
    _enable_console_ansi()
    return f"{RED}{text}{RESET}"


def _rel_path(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path


def error_category(exc: BaseException) -> str:
    """将异常归类为可读的错误类型。"""
    name = type(exc).__name__
    mod = type(exc).__module__ or ""

    try:
        import requests

        if isinstance(exc, requests.exceptions.Timeout):
            return "请求超时"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "连接失败"
        if isinstance(exc, requests.exceptions.HTTPError):
            return "远程服务器返回错误"
        if isinstance(exc, requests.exceptions.RequestException):
            return "HTTP 请求失败"
    except ImportError:
        pass

    if isinstance(exc, TimeoutError):
        return "请求超时"
    if isinstance(exc, ConnectionError):
        return "连接失败"
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10061, 10060, 10054):
        return "连接失败"
    if isinstance(exc, OSError) and "Connection" in str(exc):
        return "连接失败"
    if isinstance(exc, json.JSONDecodeError):
        return "JSON 解析失败"
    if name in ("KeyError", "IndexError", "AttributeError", "TypeError", "ValueError"):
        return "数据字段/类型错误"
    if "HTTP" in name or "http" in mod:
        return "远程服务器返回错误"
    return name


def _origin(exc: BaseException) -> tuple[str, int, str]:
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is None:
        return ("?", 0, "?")
    frame = tb.tb_frame
    return (_rel_path(frame.f_code.co_filename), tb.tb_lineno, frame.f_code.co_name)


def _remote_detail(exc: BaseException, *, max_body: int = 2000) -> str:
    try:
        import requests

        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            resp = exc.response
            body = (resp.text or "").strip()
            if len(body) > max_body:
                body = body[:max_body] + "…(truncated)"
            body_one_line = body.replace("\r", " ").replace("\n", " ")
            return f"HTTP {resp.status_code} body={body_one_line}"
    except ImportError:
        pass
    return ""


def format_http_response_body(text: str, *, max_len: int = 2000) -> str:
    """格式化 HTTP 响应体用于日志（优先 JSON）。"""
    raw = (text or "").strip()
    if not raw:
        return "(empty body)"
    try:
        parsed = json.loads(raw)
        formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        formatted = raw
    if len(formatted) > max_len:
        return formatted[:max_len] + "\n…(truncated)"
    return formatted


def format_error_detail(context: str, exc: BaseException) -> str:
    """单行错误摘要：类型 + 上下文 + 具体原因 + 报错位置。"""
    cat = error_category(exc)
    path, line, func = _origin(exc)
    msg = str(exc).strip() or repr(exc)
    remote = _remote_detail(exc)
    if remote:
        msg = f"{msg}; {remote}" if msg else remote
    parts = [f"[{cat}]", context, f"— {msg}"]
    if exc.__cause__ is not None:
        parts.append(f"(原因: {error_category(exc.__cause__)}: {exc.__cause__})")
    parts.append(f"@ {_rel_path(path)}:{line} in {func}()")
    line_out = " ".join(parts)
    if remote and "body=" in remote:
        # 响应体单独一行，避免单行过长难以阅读
        body_part = remote.split("body=", 1)[-1]
        line_out = f"{line_out}\n远程响应: {body_part}"
    return line_out


def log_caught_error(
    logger: logging.Logger,
    context: str,
    exc: BaseException | None = None,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """记录当前或指定异常，不输出完整 traceback。"""
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        logger.error(color_red(context), extra=extra)
        return
    logger.error(color_red(format_error_detail(context, exc)), extra=extra)


class RedErrorFormatter(logging.Formatter):
    """ERROR 及以上级别整行红色（未自行着色时）。"""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if record.levelno >= logging.ERROR and RED not in msg:
            return color_red(msg)
        return msg


_QUIET_LOGGER_NAMES = (
    "httpx",
    "httpcore",
    "httpcore.http11",
    "httpcore.connection",
    "urllib3",
    "urllib3.connectionpool",
    "apscheduler",
    "apscheduler.scheduler",
)


def quiet_third_party_loggers(level: int = logging.WARNING) -> None:
    """压低 httpx / urllib3 / APScheduler 等逐条 INFO 日志。"""
    for name in _QUIET_LOGGER_NAMES:
        logging.getLogger(name).setLevel(level)


def configure_app_logging(level: int = logging.INFO) -> None:
    """为根 logger 配置 stderr 输出；ERROR 红色。"""
    _enable_console_ansi()
    quiet_third_party_loggers()
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(RedErrorFormatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
    root.setLevel(level)
    root.addHandler(handler)
