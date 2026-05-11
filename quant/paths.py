"""Filesystem paths used by the new quant package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATA_DIR = Path.home() / ".quant"


def quant_data_dir() -> Path:
    """Return the canonical quant state directory.

    ``GOLDQUANT_DATA_DIR`` allows local experiments without changing code. The
    default is the runtime state directory shared by the API and quant runner.
    """

    raw = os.getenv("GOLDQUANT_DATA_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DATA_DIR


@dataclass(frozen=True)
class QuantPaths:
    data_dir: Path
    optional_file: Path
    holding_file: Path
    stoploss_file: Path
    fund_file: Path
    signal_dir: Path


def get_quant_paths(data_dir: Path | None = None) -> QuantPaths:
    root = (data_dir or quant_data_dir()).expanduser().resolve()
    return QuantPaths(
        data_dir=root,
        optional_file=root / "optional.jsonl",
        holding_file=root / "holding.jsonl",
        stoploss_file=root / "stoploss.jsonl",
        fund_file=root / "fund.md",
        signal_dir=root / "signals",
    )
