"""候选股来源初筛与通用漏斗配置（quant.yml scoring.candidate）。"""

from __future__ import annotations

from quant.config import load_scoring_config

# 三来源在 payload / builder 中使用的键
PAYLOAD_KEY_POPULARITY = "同花顺人气榜"
PAYLOAD_KEY_ZT = "涨停候选"
PAYLOAD_KEY_PKYD = "盘口异动"

SOURCE_LABEL_POPULARITY = "人气榜"
SOURCE_LABEL_ZT = "涨停池"
SOURCE_LABEL_PKYD = "盘口异动"

DEFAULT_PKYD_LABELS = ("60日新高", "60日大幅上涨")


def load_candidate_config() -> dict:
    return load_scoring_config().get("candidate") or {}


def popularity_limit(cfg: dict | None = None) -> int:
    c = cfg or load_candidate_config()
    return int(c.get("popularity_limit", 30))


def zt_min_boards(cfg: dict | None = None) -> int:
    c = cfg or load_candidate_config()
    return int(c.get("zt_min_boards", 2))


def pkyd_prefilter_pool(cfg: dict | None = None) -> int:
    c = cfg or load_candidate_config()
    return int(c.get("pkyd_prefilter_pool", 60))


def pkyd_dual_tag_limit(cfg: dict | None = None) -> int:
    c = cfg or load_candidate_config()
    return int(c.get("pkyd_dual_tag_limit", 20))


def pkyd_labels(cfg: dict | None = None) -> tuple[str, ...]:
    c = cfg or load_candidate_config()
    raw = c.get("pkyd_labels") or list(DEFAULT_PKYD_LABELS)
    if isinstance(raw, (list, tuple)):
        return tuple(str(x).strip() for x in raw if str(x).strip())
    return DEFAULT_PKYD_LABELS
