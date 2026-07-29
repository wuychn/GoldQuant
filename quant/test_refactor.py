"""重构架构回归测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from quant.data.sources.rate_limit import reset_limiters, with_limit
from quant.yml_schema import validate_quant_config
from quant.config import load_quant_config


def test_quant_yml_schema_valid():
    cfg = load_quant_config()
    errors = validate_quant_config(cfg)
    assert errors == [], f"quant.yml invalid: {errors}"


def test_fetch_mode_fixture(tmp_path, monkeypatch):
    from quant import data_fetch

    fixture = {"code": 0, "data": {"大盘指数": []}}
    p = tmp_path / "during_market.json"
    p.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(data_fetch, "_PROJECT_ROOT", tmp_path.parent)
    monkeypatch.setattr(data_fetch, "fixture_path_for_mode", lambda m: p)
    monkeypatch.setattr(data_fetch, "fixture_mode", lambda: True)
    raw = data_fetch.fetch_mode("during_market")
    assert raw["data"]["大盘指数"] == []


def test_with_limit_serializes():
    reset_limiters()
    calls: list[int] = []

    def work():
        calls.append(1)
        return 42

    assert with_limit("akshare", work) == 42
    assert len(calls) == 1


def test_build_pre_market_payload_mocked():
    pytest.importorskip("akshare")
    import asyncio

    from quant.services.market.payload import build_pre_market_payload

    settings = type(
        "S",
        (),
        {"quant_bulk_row_limit": lambda self: None, "quant_hot_list_limit": lambda self: 30},
    )()
    with (
        patch("quant.services.market.payload.fetch_index_spot", new=AsyncMock(return_value=[])),
        patch("quant.services.market.payload.fetch_zqxy", new=AsyncMock(return_value={})),
        patch("quant.services.market.payload.fetch_ztgk", new=AsyncMock(return_value={})),
        patch(
            "quant.services.market.enrich.enrich_optional_and_holding",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        br = asyncio.run(build_pre_market_payload(settings))
        assert "大盘指数" in br.payload


def test_runner_module_exists():
    pytest.importorskip("akshare")
    import importlib.util

    assert importlib.util.find_spec("quant.ops.runner") is not None
    assert importlib.util.find_spec("quant.jobs.market_jobs") is not None
