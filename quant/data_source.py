"""Data loading helpers shared by CLI and legacy runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import requests


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURE_ROOT = PACKAGE_ROOT / "data"

Mode = Literal["news", "pre_market", "during_market", "post_market_lunch", "post_market_evening"]
DataSource = Literal["local", "remote"]

VALID_MODES: tuple[str, ...] = (
    "news",
    "pre_market",
    "during_market",
    "post_market_lunch",
    "post_market_evening",
)
VALID_DATA_SOURCES: tuple[str, ...] = ("local", "remote")
DEFAULT_BASE_URL = "http://localhost:8085"

MODE_ENDPOINTS = {
    "news": "/api/v1/quant/market/news",
    "pre_market": "/api/v1/quant/market/pre_market",
    "during_market": "/api/v1/quant/market/during_market",
    "post_market_lunch": "/api/v1/quant/market/post_market",
    "post_market_evening": "/api/v1/quant/market/post_market",
}


def default_data_source() -> str:
    raw = os.getenv("GOLDQUANT_DATA_SOURCE", "remote").strip().lower()
    aliases = {"fixture": "local", "live": "remote"}
    normalized = aliases.get(raw, raw)
    return normalized if normalized in VALID_DATA_SOURCES else "remote"


def default_base_url() -> str:
    return os.getenv("GOLDQUANT_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")


def read_text_auto(path: str | Path) -> str:
    with Path(path).open("rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_json_file(path: str | Path) -> dict[str, Any]:
    data = json.loads(read_text_auto(path))
    return data if isinstance(data, dict) else {}


def fetch_live_data(mode: str, *, base_url: str | None = None) -> dict[str, Any]:
    endpoint = MODE_ENDPOINTS[mode]
    root = (base_url or default_base_url()).rstrip("/")
    resp = requests.get(f"{root}{endpoint}", timeout=600)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def fixture_path_for_mode(project_root: str | Path, mode: str) -> Path:
    return Path(project_root) / mode


def load_mode_data(
    mode: str,
    *,
    source: str,
    project_root: str | Path = DEFAULT_FIXTURE_ROOT,
    base_url: str | None = None,
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"未知模式: {mode}")
    if source not in VALID_DATA_SOURCES:
        raise ValueError(f"未知数据源: {source}")
    if source == "remote":
        return fetch_live_data(mode, base_url=base_url)
    root = project_root or DEFAULT_FIXTURE_ROOT
    return load_json_file(fixture_path_for_mode(root, mode))
