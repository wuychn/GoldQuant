"""问财 Hexin-V 生成（基于 chameleon.js，非 akshare ths.js）。"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path

import py_mini_racer

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_CHAMELEON_JS = _ASSETS_DIR / "chameleon.iwencai.min.js"
_RUNTIME_JS = _ASSETS_DIR / "iwencai_hexin_runtime.js"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _inject_token_server_time(chameleon_js: str) -> str:
    """chameleon 依赖 TOKEN_SERVER_TIME；运行时替换为当前秒级时间戳。"""
    server_time = time.time()
    if re.search(r"^\s*var\s+TOKEN_SERVER_TIME\s*=", chameleon_js):
        return re.sub(
            r"^\s*var\s+TOKEN_SERVER_TIME\s*=[^;]+;",
            f"var TOKEN_SERVER_TIME={server_time};",
            chameleon_js,
            count=1,
        )
    return f"var TOKEN_SERVER_TIME={server_time};\n{chameleon_js}"


@lru_cache(maxsize=1)
def _build_iwencai_hexin_ctx() -> py_mini_racer.MiniRacer:
    if not _CHAMELEON_JS.is_file():
        raise FileNotFoundError(f"缺少问财 chameleon 脚本: {_CHAMELEON_JS}")
    if not _RUNTIME_JS.is_file():
        raise FileNotFoundError(f"缺少问财 Hexin-V 运行环境: {_RUNTIME_JS}")

    ctx = py_mini_racer.MiniRacer()
    ctx.eval(_load_text(_RUNTIME_JS))
    chameleon_js = _inject_token_server_time(_load_text(_CHAMELEON_JS))
    ctx.eval(chameleon_js)
    ctx.eval(
        """
        function getIwencaiHexinV() {
            if (typeof __iwencaiHexinV !== 'function') {
                throw new Error('chameleon export __iwencaiHexinV missing');
            }
            var token = __iwencaiHexinV();
            if (token && String(token).length > 0) {
                return String(token);
            }
            throw new Error('chameleon did not produce cookie v');
        }
        """
    )
    return ctx


def get_iwencai_hexin_v() -> str:
    """
    生成问财接口所需的 Hexin-V（与 cookie ``v`` 一致）。

    实现方式：在补全的浏览器环境中执行问财页面使用的 ``chameleon.js``，
    调用其导出的 ``__iwencaiHexinV``（内部即 ``M.update()``）生成 token。
    """
    ctx = _build_iwencai_hexin_ctx()
    ctx.eval(f"TOKEN_SERVER_TIME={time.time()};")
    token = ctx.call("getIwencaiHexinV")
    if not token or not isinstance(token, str):
        raise RuntimeError("问财 Hexin-V 生成失败：返回值为空")
    return token
