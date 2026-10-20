"""情绪开关：四项 + 高位批量跌停辅助。红灯不开新仓。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Emotion:
    dt_n: int = 0
    zb_n: int = 0
    zt_n: int = 0
    zb_rate: float | None = None
    promo_2to3: float | None = None
    n_2board: int = 0
    n_2to3: int = 0
    y_zt_prem: float | None = None
    high_dt_n: int = 0
    new_entry: str = "open"  # open | plan_only | block
    weight_scale: float = 1.0  # 2进3 黄灯轻仓 = 0.5
    reasons: list[str] = field(default_factory=list)
    lights: dict[str, str] = field(default_factory=dict)

    @property
    def allow_new(self) -> bool:
        return self.new_entry != "block"

    @property
    def plan_only(self) -> bool:
        return self.new_entry == "plan_only"


def _zb_rate(zt_n: int, zb_n: int) -> float | None:
    den = zt_n + zb_n
    if den <= 0:
        return None
    return zb_n / den


def evaluate_emotion(
    *,
    dt_n: int,
    zt_n: int,
    zb_n: int,
    n_2board: int,
    n_2to3: int,
    y_zt_prem: float | None,
    high_dt_n: int = 0,
) -> Emotion:
    """红灯=停手；黄灯=只做计划内/轻仓/降预期。"""
    e = Emotion(
        dt_n=int(dt_n),
        zb_n=int(zb_n),
        zt_n=int(zt_n),
        n_2board=int(n_2board),
        n_2to3=int(n_2to3),
        y_zt_prem=y_zt_prem,
        high_dt_n=int(high_dt_n),
    )
    e.zb_rate = _zb_rate(e.zt_n, e.zb_n)
    if e.n_2board > 0:
        e.promo_2to3 = e.n_2to3 / e.n_2board

    red: list[str] = []
    yellow: list[str] = []

    if e.dt_n >= 20:
        red.append(f"跌停{e.dt_n}≥20")
        e.lights["dt"] = "red"
    elif e.dt_n >= 5:
        yellow.append(f"跌停{e.dt_n}预警")
        e.lights["dt"] = "yellow"
    elif e.dt_n >= 3:
        yellow.append(f"跌停{e.dt_n}未到安全（<3）")
        e.lights["dt"] = "yellow"
    else:
        e.lights["dt"] = "green"

    if e.zb_rate is not None:
        if e.zb_rate > 0.50:
            red.append(f"炸板率{e.zb_rate:.0%}>50%")
            e.lights["zb"] = "red"
        elif e.zb_rate >= 0.25:
            yellow.append(f"炸板率{e.zb_rate:.0%}只做计划内")
            e.lights["zb"] = "yellow"
        else:
            e.lights["zb"] = "green"
    else:
        e.lights["zb"] = "unknown"

    if e.promo_2to3 is not None:
        if e.promo_2to3 < 0.20:
            red.append(f"2进3 {e.promo_2to3:.0%}<20%")
            e.lights["promo"] = "red"
        elif e.promo_2to3 < 0.40:
            yellow.append(f"2进3 {e.promo_2to3:.0%}轻仓")
            e.lights["promo"] = "yellow"
        else:
            e.lights["promo"] = "green"
    else:
        e.lights["promo"] = "unknown"

    if e.y_zt_prem is not None:
        if e.y_zt_prem < 0:
            red.append(f"昨涨停溢价{e.y_zt_prem:.1f}%<0")
            e.lights["prem"] = "red"
        elif e.y_zt_prem < 3:
            yellow.append(f"昨涨停溢价{e.y_zt_prem:.1f}%降预期")
            e.lights["prem"] = "yellow"
        else:
            e.lights["prem"] = "green"
    else:
        e.lights["prem"] = "unknown"

    if e.high_dt_n >= 3:
        yellow.append(f"高位股批量跌停{e.high_dt_n}")
        e.lights["high_dt"] = "yellow"
    else:
        e.lights["high_dt"] = "green"

    e.reasons = red + yellow
    if e.lights.get("promo") == "yellow":
        e.weight_scale = 0.5
    if red:
        e.new_entry = "block"
    elif yellow:
        e.new_entry = "plan_only"
    else:
        e.new_entry = "open"
    return e
