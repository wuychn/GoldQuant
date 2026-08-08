"""scripts 共用的 ``--home`` 参数与 QUANT_HOME 覆盖上下文。"""

from __future__ import annotations

from argparse import ArgumentParser
from contextlib import nullcontext
from pathlib import Path
from quant.store.paths import override_quant_home


_HOME_HELP = (
    "quant-home 根目录（含 store/）；"
    "默认当前 QUANT_HOME / GOLDQUANT_QUANT_HOME_DIR / ~/.quant"
)


def add_home_argument(ap: ArgumentParser) -> None:
    """向 ArgumentParser 注册可选 ``--home``。"""
    ap.add_argument("--home", default=None, help=_HOME_HELP)


def resolve_home(home: str | Path | None) -> Path | None:
    """解析 ``--home``；未传则返回 None（沿用当前 quant_home）。"""
    if home is None or str(home).strip() == "":
        return None
    return Path(home).expanduser()


def home_context(home: str | Path | None):
    """临时覆盖 quant_home；未指定 home 时为 nullcontext。"""
    root = resolve_home(home)
    if root is None:
        return nullcontext()
    return override_quant_home(root)


def home_cli_args(home: str | Path | None) -> list[str]:
    """把 ``--home`` 转成可转发给子进程的 argv 片段。"""
    root = resolve_home(home)
    if root is None:
        return []
    return ["--home", str(root)]
