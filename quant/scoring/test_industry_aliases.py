"""东财 jbxx 行业 → 同花顺行业榜映射。"""

from __future__ import annotations

from quant.scoring.industry_aliases import expand_industries, reload_industry_aliases_cache


def test_expand_industries_maps_em_to_ths() -> None:
    reload_industry_aliases_cache()
    out = expand_industries({"塑料", "专用行业X"})
    assert "塑料制品" in out
    assert "塑料" in out
    assert "专用行业X" in out


def test_exact_match_needs_no_alias() -> None:
    reload_industry_aliases_cache()
    out = expand_industries({"元件", "半导体"})
    assert out == {"元件", "半导体"}


def test_package_aliases_only(tmp_path, monkeypatch) -> None:
    from quant.scoring import industry_aliases as mod

    pkg = tmp_path / "industry_aliases.yml"
    pkg.write_text(
        "aliases:\n  元件:\n    - 电子元件\n  小金属:\n    - 稀有金属\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_PACKAGE_ALIASES", pkg)
    mod.reload_industry_aliases_cache()

    out = mod.expand_industries({"稀有金属", "电子元件"})
    assert out == {"稀有金属", "小金属", "电子元件", "元件"}
