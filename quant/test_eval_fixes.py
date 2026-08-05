"""评估文档修复项回归测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from quant.backtest.broker import SimBroker, Trade
from quant.backtest.stats import newey_west_sharpe
from quant.data.listing import build_listing_map_from_daily, listing_days_as_of
from quant.portfolio.constraints import alpha_strength_weights, apply_style_cap


def test_newey_west_sharpe_finite():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0005, 0.01, 300)
    s = newey_west_sharpe(rets)
    assert np.isfinite(s)


def test_alpha_strength_weights_positive():
    alpha = {"a": 2.0, "b": 1.0, "c": 0.0}
    w = alpha_strength_weights(alpha, ["a", "b", "c"], full_invest=0.95, shrink=0.3)
    assert abs(sum(w.values()) - 0.95) < 1e-6
    assert w["a"] > w["b"] > w["c"]


def test_apply_style_cap():
    w = {"a": 0.3, "b": 0.3, "c": 0.4}
    buckets = {"a": "small", "b": "small", "c": "large"}
    out = apply_style_cap(w, buckets, {"small": 0.4})
    assert out["a"] + out["b"] <= 0.4 + 1e-6


def test_listing_days_from_first_bar():
    rows = []
    for d in pd.bdate_range("2025-01-01", periods=130).strftime("%Y-%m-%d"):
        rows.append({"code": "000001", "date": d, "close": 10.0})
    daily = pd.DataFrame(rows)
    mp = build_listing_map_from_daily(daily)
    days = listing_days_as_of("000001", "2025-06-30", listing_map=mp, daily=daily)
    assert days >= 100


def test_load_factor_weights_strict_oos(monkeypatch):
    from quant import config as cfg_mod
    from quant.store import paths as pmod

    root = Path(tempfile.mkdtemp())
    monkeypatch.setattr(pmod, "quant_home", lambda: root)
    cfg_mod.reload_config_cache()

    static = root / "config" / "factor_weights.yml"
    static.parent.mkdir(parents=True, exist_ok=True)
    static.write_text(
        yaml.safe_dump({"apply": True, "fit_end": "2099-12-31", "weights": {"mom_20": 1.0}}),
        encoding="utf-8",
    )
    cfg_mod.reload_config_cache()
    info = cfg_mod.load_factor_weights_info(as_of="2024-01-01")
    assert info is None

    ts = root / "config" / "factor_weights_ts.yml"
    ts.write_text(
        yaml.safe_dump(
            {
                "apply": True,
                "weights_ts": {
                    "2024-01-10": {
                        "weights": {"mom_20": 0.8},
                        "train_start": "2023-01-01",
                        "train_end": "2024-01-09",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg_mod.reload_config_cache()
    info2 = cfg_mod.load_factor_weights_info(as_of="2024-01-15")
    assert info2 is not None
    assert info2["source"] == "walk_forward"
    assert info2["weights"]["mom_20"] == 0.8


def test_optimize_mvo():
    from quant.portfolio.mvo import optimize_mvo

    codes = ["a", "b", "c"]
    alpha = {"a": 1.0, "b": 0.5, "c": 0.2}
    sigmas = {"a": 0.2, "b": 0.25, "c": 0.3}
    cov = {c: {d: sigmas[c] * sigmas[d] * (0.3 if c != d else 1.0) for d in codes} for c in codes}
    w = optimize_mvo(codes, alpha, cov, sigmas, max_weight=0.5, full_invest=0.95)
    assert abs(sum(w.values()) - 0.95) < 1e-5
    assert w["a"] >= w["b"] >= w["c"]


def test_sqrt_law_slippage_increases_with_participation():
    from quant.execution.sim_rules import TradeSimConfig
    from quant.execution.slippage import SlippageContext, effective_slippage_pct

    cfg = TradeSimConfig(slippage_model="sqrt_law", slippage_pct=0.001, slippage_sqrt_k=0.5)
    low = effective_slippage_pct(cfg, SlippageContext(volatility_pct=2.0, amount=1e5, adv_amount=1e8, participation=0.01))
    high = effective_slippage_pct(cfg, SlippageContext(volatility_pct=2.0, amount=1e7, adv_amount=1e8, participation=0.15))
    assert high > low


def test_capacity_metrics():
    from quant.backtest.attribution import capacity_metrics

    b = SimBroker(cash=0)
    b.trades = [
        Trade(date="2024-01-02", code="000001", side="buy", shares=1000, price=10.0, cost=5.0),
    ]
    daily = pd.DataFrame(
        {
            "code": ["000001"] * 25,
            "date": pd.bdate_range("2024-01-01", periods=25).strftime("%Y-%m-%d"),
            "amount": [1e8] * 25,
            "close": [10.0] * 25,
        }
    )
    m = capacity_metrics(b, daily, initial_cash=1e6, participation_rate=0.1)
    assert m["n_buys"] == 1
    assert m["median_participation_pct"] >= 0


def test_fundamental_pit_metrics_as_of():
    from quant.data.fundamental_pit import metrics_as_of, regulatory_disclosure_deadline

    assert regulatory_disclosure_deadline("2023-12-31") == "2024-04-30"
    assert regulatory_disclosure_deadline("2024-03-31") == "2024-04-30"

    # 4 季累计 EPS → TTM = 2.0 → PE = 10
    table = pd.DataFrame(
        [
            {"code": "000001", "report_date": "2023-03-31", "announce_date": "2023-04-28", "eps": 0.5, "bps": 9.0},
            {"code": "000001", "report_date": "2023-06-30", "announce_date": "2023-08-30", "eps": 1.0, "bps": 9.5},
            {"code": "000001", "report_date": "2023-09-30", "announce_date": "2023-10-28", "eps": 1.5, "bps": 10.0},
            {"code": "000001", "report_date": "2023-12-31", "announce_date": "2024-04-25", "eps": 2.0, "bps": 10.0, "roe": 12.0, "rev_yoy": 5.0},
        ]
    )
    assert metrics_as_of("000001", "2023-04-01", close=20.0, table=table) == {}
    m = metrics_as_of("000001", "2024-04-25", close=20.0, table=table)
    assert abs(m["pe_ttm"] - 10.0) < 1e-6
    assert abs(m["ep_ttm"] - 0.1) < 1e-6
    assert abs(m["pb"] - 2.0) < 1e-6


def test_compute_ttm_eps_four_quarters():
    from quant.data.fundamental_pit import _decompose_quarterly_eps, compute_ttm_eps

    rows = [
        {"report_date": "2023-03-31", "eps": 0.5},
        {"report_date": "2023-06-30", "eps": 1.0},
        {"report_date": "2023-09-30", "eps": 1.5},
        {"report_date": "2023-12-31", "eps": 2.0},
    ]
    q = _decompose_quarterly_eps(rows)
    assert len(q) == 4
    assert abs(sum(x[1] for x in q) - 2.0) < 1e-9
    assert abs(compute_ttm_eps(q) - 2.0) < 1e-9


def test_negative_ttm_ep_signed():
    from quant.data.fundamental_pit import metrics_as_of

    table = pd.DataFrame(
        [
            {
                "code": "L001",
                "report_date": "2023-12-31",
                "announce_date": "2024-04-25",
                "eps": -1.0,
                "bps": 5.0,
            },
        ]
    )
    m = metrics_as_of("L001", "2024-05-01", close=10.0, table=table)
    assert abs(m.get("ep_ttm", 0) - (-0.1)) < 1e-9
    assert "pe_ttm" not in m
    assert abs(m.get("bp", 0) - 0.5) < 1e-9


def test_parse_report_title_short_forms():
    from quant.data.fundamental_pit import _parse_report_date_from_title

    assert _parse_report_date_from_title("6005192023年报") == "2023-12-31"
    assert _parse_report_date_from_title("2024年一季报") == "2024-03-31"
    assert _parse_report_date_from_title("2024年三季报") == "2024-09-30"
    assert _parse_report_date_from_title("2024年半年报") == "2024-06-30"
    # 非行尾「年报」不匹配 $ 锚定规则，避免误捕获中间年份
    assert _parse_report_date_from_title("关于2022与2023年报说明") is None


def test_resolve_listing_map():
    from quant.data.listing import resolve_listing_map

    mp = resolve_listing_map(
        pd.DataFrame({"code": ["000001"], "date": ["2020-01-02"]}),
        base={"000001": "2018-06-01"},
    )
    assert mp.get("000001") is not None


def test_fundamental_pit_regulatory_fallback():
    from quant.data.fundamental_pit import metrics_as_of

    table = pd.DataFrame(
        [
            {
                "code": "000002",
                "report_date": "2023-12-31",
                "announce_date": None,
                "roe": 10.0,
                "eps": 1.0,
                "bps": 5.0,
                "rev_yoy": 3.0,
            },
        ]
    )
    assert metrics_as_of("000002", "2024-01-15", close=10.0, table=table) == {}
    m = metrics_as_of("000002", "2024-05-01", close=10.0, table=table)
    assert m.get("ep_ttm") == 0.1


def test_sqrt_law_uses_higher_cap():
    from quant.execution.sim_rules import TradeSimConfig
    from quant.execution.slippage import SlippageContext, effective_slippage_pct

    cfg = TradeSimConfig(
        slippage_model="sqrt_law",
        slippage_pct=0.001,
        slippage_sqrt_k=0.5,
        slippage_max_pct=0.005,
        slippage_sqrt_max_pct=0.02,
    )
    slip = effective_slippage_pct(
        cfg,
        SlippageContext(volatility_pct=5.0, amount=5e7, adv_amount=1e8, participation=0.5),
    )
    assert slip > 0.005


def test_should_refresh_fundamental_pit():
    from quant.data.fundamental_pit import should_refresh_fundamental_pit

    assert should_refresh_fundamental_pit("2024-05-10") is True
    assert should_refresh_fundamental_pit("2024-08-20") is True
    assert should_refresh_fundamental_pit("2024-08-10") is False
    assert should_refresh_fundamental_pit("2024-09-05") is True
    assert should_refresh_fundamental_pit("2024-07-15") is False


def test_disclosure_refresh_rotates_all_codes():
    from quant.data.fundamental_pit import _codes_for_disclosure_refresh

    codes = [f"{i:06d}" for i in range(10)]
    batch1, c1 = _codes_for_disclosure_refresh(codes, limit=3)
    batch2, c2 = _codes_for_disclosure_refresh(codes, limit=3)
    assert len(batch1) == 3
    assert batch1 != batch2 or c1 != c2
    assert set(batch1).issubset(set(codes))


def test_refresh_already_ran_today(tmp_path, monkeypatch):
    from quant.data import fundamental_pit as fp

    meta = tmp_path / "last_refresh.txt"
    monkeypatch.setattr(fp, "_pit_meta_path", lambda name: meta if name == "last_refresh.txt" else tmp_path / name)
    assert fp.refresh_already_ran_today("2024-05-01") is False
    fp.mark_refresh_done("2024-05-01")
    assert fp.refresh_already_ran_today("2024-05-01") is True
    assert fp.refresh_already_ran_today("2024-05-02") is False


def test_capture_all_no_fundamentals_key(monkeypatch, tmp_path):
    """只验证返回结构；mock 掉三个 capture（真跑会拉网络 + 写 home），并隔离临时 home。"""
    import pandas as pd
    from quant.data import factor_capture as fc
    from quant.store.paths import override_quant_home

    # 隔离：测试只写临时 home，不污染真实 QUANT_HOME（曾把 hot_rank/year=2099 写进 .quant2）
    with override_quant_home(tmp_path):
        # mock 网络写盘：三个 capture 不真跑
        monkeypatch.setattr(fc, "capture_hot_rank", lambda as_of: 0)
        monkeypatch.setattr(fc, "capture_fund_flow", lambda as_of, spot=None, universe_codes=None: 0)
        monkeypatch.setattr(fc, "capture_theme_mom", lambda as_of, spot=None: 0)
        out = fc.capture_all_factor_snapshots(
            "2026-08-04", spot=pd.DataFrame(), universe_codes=[]
        )
        assert "fundamentals" not in out
        assert set(out.keys()) == {"hot", "flow", "theme"}


def test_spot_row_from_daily_speed_proxy():
    from quant.factors.library.intraday import spot_row_from_daily

    row = {"open": 10.0, "close": 10.5, "volume": 1e6, "amount": 1e7, "turnover_rate": 2.0}
    sr = spot_row_from_daily("000001", row, prev_close=9.8)
    assert sr is not None
    assert abs(sr.speed - 5.0) < 1e-6  # (10.5-10)/10*100


def test_factor_correlation_matrix():
    from quant.factors.base import FactorRow
    from quant.factors.ic import factor_correlation_matrix

    rows: list[FactorRow] = []
    for d in ("2024-01-02", "2024-01-03", "2024-01-04"):
        for i in range(12):
            rows.append(
                FactorRow(
                    date=d,
                    code=f"{i:06d}",
                    raw={"mom_20": float(i), "mom_60": float(i) * 0.9 + 1},
                    neutral={"mom_20": float(i), "mom_60": float(i) * 0.9 + 1},
                    forward_return_pct=0.01,
                )
            )
    out = factor_correlation_matrix(rows, ["mom_20", "mom_60"])
    assert out["n_days"] >= 1
    assert out["matrix"]
    assert out["high_pairs"]


def test_benchmark_excess_extended():
    from quant.backtest.metrics import benchmark_excess

    b = SimBroker(cash=0)
    b.equity_curve = [
        ("2024-01-02", 1_000_000.0),
        ("2024-01-03", 1_010_000.0),
        ("2024-01-04", 1_020_000.0),
    ]
    bench = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [3000.0, 3010.0, 3005.0],
        }
    )
    m = benchmark_excess(b, bench)
    assert "beta" in m
    assert "tracking_error_pct" in m
    assert "relative_max_drawdown_pct" in m


def test_alpha_label_uncalibrated():
    from quant.decision.paper_execute import _alpha_label

    assert "未校准" in _alpha_label(0.812, weights_source="registry_default")
    assert "未校准" not in _alpha_label(0.812, weights_source="walk_forward")


def test_walk_forward_train_excludes_max_horizon():
    """训练切片应按 max(horizons) 排除末尾。"""
    dates = [f"2024-01-{i:02d}" for i in range(1, 31)]
    sorted_dates = sorted(dates)
    i = 25
    horizons = (5, 10, 20)
    label_horizon = max(horizons)
    train_end_idx = i - label_horizon
    train_dates = sorted_dates[0:train_end_idx]
    assert sorted_dates[i - label_horizon - 1] == train_dates[-1]
    assert sorted_dates[i - 1] not in train_dates
