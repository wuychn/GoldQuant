"""行业映射草稿生成。"""

from __future__ import annotations

from quant.scoring.industry_aliases_draft import build_alias_draft, normalize_industry_name, propose_ths_match


def test_normalize_strips_roman_suffix() -> None:
    assert normalize_industry_name("IT服务Ⅱ") == "IT服务"


def test_propose_normalize_match() -> None:
    ths = ["IT服务", "元件", "塑料制品"]
    ths_set = set(ths)
    canonical, method, score = propose_ths_match("IT服务Ⅱ", ths, ths_set)
    assert canonical == "IT服务"
    assert method == "normalize"
    assert score >= 0.9


def test_propose_exact_returns_same_name() -> None:
    ths = ["元件", "半导体"]
    canonical, method, score = propose_ths_match("元件", ths, set(ths))
    assert canonical == "元件"
    assert method == "exact"
    assert score == 1.0


def test_build_alias_draft_groups_by_ths() -> None:
    ths = ["IT服务", "元件", "塑料制品", "食品加工制造", "光学光电子", "黑色家电"]
    em = ["IT服务Ⅱ", "塑料", "休闲食品", "元件", "光学元件", "其他家电Ⅱ"]
    aliases, meta = build_alias_draft(ths, em, min_score=0.72)
    assert "IT服务" in aliases
    assert "IT服务Ⅱ" in aliases["IT服务"]
    assert "光学元件" in aliases["光学光电子"]
    assert "光学元件" not in aliases.get("元件", [])
    assert "被动元件" not in sum(aliases.values(), []) or "元件" in aliases
    assert "其他家电Ⅱ" in aliases["黑色家电"]
    assert "元件" in aliases
    assert "元件" in aliases["元件"]
    assert "元件" in meta["exact_matches"]
    assert "休闲食品" in aliases.get("食品加工制造", []) or "休闲食品" in meta["em_only"]
