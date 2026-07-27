"""盘中因子 + compose_intraday_alpha 单测。"""

from __future__ import annotations

import pytest

from quant.factors.compose import compose_intraday_alpha
from quant.factors.library.intraday import (
    SpotRow,
    intraday_strength,
    spot_row_from_dict,
    volume_ratio,
)


def _spot(
    code: str,
    last: float,
    open_: float,
    *,
    pc: float | None = None,
    pct: float = 0.0,
    vr: float = 1.0,
    speed: float = 0.0,
    turnover: float = 1.0,
) -> SpotRow:
    return SpotRow(code, last, open_, pc if pc is not None else open_, pct, 0.0, 0.0, vr, turnover, speed)


def test_intraday_strength():
    assert intraday_strength(_spot("a", 11.0, 10.0)) == pytest.approx(0.1)
    assert intraday_strength(_spot("a", 9.0, 10.0)) == pytest.approx(-0.1)
    assert intraday_strength(_spot("a", 10.0, 0.0)) is None  # open<=0


def test_volume_ratio():
    assert volume_ratio(_spot("a", 10, 10, vr=2.0)) == 2.0
    assert volume_ratio(_spot("a", 10, 10, vr=0.0)) is None  # <=0 视为缺失


def test_spot_row_from_dict():
    sr = spot_row_from_dict(
        {"code": "000001", "close": 10.5, "open": 10.0, "pct": 1.2, "vol_ratio": 1.5, "speed": 0.3}
    )
    assert sr is not None
    assert sr.last == 10.5
    assert sr.open == 10.0
    assert sr.pct == 1.2
    assert sr.vol_ratio == 1.5
    assert sr.speed == 0.3
    # 缺关键价 → None
    assert spot_row_from_dict({"code": "", "close": 10}) is None
    assert spot_row_from_dict({"code": "x", "close": 0}) is None
    assert spot_row_from_dict({"code": "x"}) is None


def test_compose_intraday_alpha_ranking():
    """放量+走强+加速的票 alpha_z 应高于平淡票。"""
    rows = [
        _spot("strong", 11.0, 10.0, pct=2.0, vr=2.5, speed=0.5, turnover=3.0),
        _spot("weak", 9.5, 10.0, pct=-1.0, vr=0.5, speed=-0.3, turnover=0.5),
        _spot("mid", 10.1, 10.0, pct=0.1, vr=1.0, speed=0.0, turnover=1.0),
    ]
    z = compose_intraday_alpha(rows)
    assert z["strong"] > z["weak"]
    assert z["strong"] > z["mid"] > z["weak"]


def test_compose_intraday_alpha_single_row_zero():
    """单只票截面 z-score 无区分度 → alpha_z ≈ 0。"""
    z = compose_intraday_alpha([_spot("a", 10, 10)])
    assert "a" in z
    assert abs(z["a"]) < 1e-9


def test_compose_intraday_alpha_threshold_filter():
    """验证 α_z≥1.0 的触发过滤：强票应被选中，弱票不选。"""
    rows = [
        _spot("strong", 11.5, 10.0, pct=3.0, vr=3.0, speed=0.8, turnover=4.0),
        _spot("weak", 9.0, 10.0, pct=-2.0, vr=0.3, speed=-0.5, turnover=0.3),
        _spot("mid1", 10.2, 10.0, pct=0.5, vr=1.2, speed=0.1, turnover=1.2),
        _spot("mid2", 9.9, 10.0, pct=-0.2, vr=0.9, speed=-0.05, turnover=0.9),
    ]
    z = compose_intraday_alpha(rows)
    triggered = [r.code for r in rows if z.get(r.code, 0.0) >= 1.0]
    assert "strong" in triggered
    assert "weak" not in triggered
