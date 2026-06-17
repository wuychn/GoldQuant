"""行业映射人工规则单测。"""

from __future__ import annotations

from quant.scoring.industry_alias_rules import (
    FORBIDDEN_PAIRS,
    MANUAL_HINTS,
    apply_alias_corrections,
    substring_match_allowed,
)
from quant.scoring.industry_aliases_draft import propose_ths_match


def test_substring_blocks_optical_vs_component() -> None:
    ths = ["元件", "光学光电子", "被动元件"]
    canonical, method, _ = propose_ths_match("光学元件", ths, set(ths))
    assert canonical == "光学光电子"
    assert method == "manual_hint"


def test_substring_allows_passive_component() -> None:
    ths = ["元件", "光学光电子"]
    canonical, method, _ = propose_ths_match("被动元件", ths, set(ths))
    assert canonical == "元件"
    assert method == "manual_hint"


def test_forbidden_laser_not_pv() -> None:
    assert ("激光设备", "光伏设备") in FORBIDDEN_PAIRS
    ths = ["光伏设备", "专用设备"]
    canonical, method, score = propose_ths_match("激光设备", ths, set(ths))
    assert canonical == "专用设备"
    assert method == "manual_hint"
    assert score >= 0.9


def test_apply_corrections_moves_em_to_hint_bucket() -> None:
    aliases = {
        "元件": ["光学元件", "被动元件"],
        "光伏设备": ["激光设备"],
        "白酒": ["非白酒"],
    }
    apply_alias_corrections(aliases)
    assert "光学元件" not in aliases["元件"]
    assert "光学元件" in aliases["光学光电子"]
    assert "被动元件" in aliases["元件"]
    assert "激光设备" in aliases["专用设备"]
    assert "非白酒" in aliases["饮料制造"]
    assert "非白酒" not in aliases.get("白酒", [])


def test_substring_guard_blocks_other_appliance_to_other_electronics() -> None:
    assert not substring_match_allowed("其他家电Ⅱ", "其他电子")
    assert MANUAL_HINTS["其他家电Ⅱ"] == "黑色家电"
