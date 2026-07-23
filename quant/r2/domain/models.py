"""R2 领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Regime(str, Enum):
    STRONG = "强势"
    NEUTRAL = "震荡"
    WEAK = "弱势"


class Lifecycle(str, Enum):
    SPROUT = "萌芽"
    SPREAD = "扩散"
    CLIMAX = "高潮"
    FADE = "退潮"


class PoolStage(str, Enum):
    TRACKING = "tracking"
    COMBAT = "combat"


@dataclass
class SectorRow:
    name: str
    section: str
    rank_gain: int | None = None
    rank_fund: int | None = None
    change_pct: float | None = None
    net_flow: float | None = None
    days_on_gain_board: int = 0
    lifecycle: Lifecycle = Lifecycle.SPROUT
    defensive: bool = False
    eligible: bool = True
    note: str = ""


@dataclass
class PoolMember:
    code: str
    name: str
    stage: PoolStage
    structure_score: float = 0.0
    sector_tags: list[str] = field(default_factory=list)
    first_seen: str = ""
    promoted_at: str = ""
    reason: str = ""
    snapshot: dict = field(default_factory=dict)


@dataclass
class RegimeSnapshot:
    regime: Regime
    zt_count: int = 0
    up_count: int = 0
    down_count: int = 0
    index_chg: float | None = None
    combat_cap: int = 10
    allow_new_combat: bool = True
    note: str = ""
