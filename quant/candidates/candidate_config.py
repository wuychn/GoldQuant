"""候选股来源初筛与通用漏斗配置（quant.yml scoring.candidate）。"""

from __future__ import annotations

from quant.config import load_scoring_config

# 三来源 + 形态榜在 payload / builder 中使用的键
PAYLOAD_KEY_POPULARITY = "同花顺人气榜"
PAYLOAD_KEY_ZT = "涨停候选"
PAYLOAD_KEY_CXG = "创新高"
PAYLOAD_KEY_LXSZ = "持续上涨"
PAYLOAD_KEY_CXFL = "持续放量"
PAYLOAD_KEY_LJQS = "量价齐升"

THS_RANK_PAYLOAD_KEYS = (
    PAYLOAD_KEY_CXG,
    PAYLOAD_KEY_LXSZ,
    PAYLOAD_KEY_CXFL,
    PAYLOAD_KEY_LJQS,
)

SOURCE_LABEL_POPULARITY = "人气榜"
SOURCE_LABEL_ZT = "涨停池"
SOURCE_LABEL_THS_RANK = "形态榜"

DEFAULT_CXG_LABELS = ("创月新高", "半年新高", "一年新高", "历史新高")


def load_candidate_config() -> dict:
    return load_scoring_config().get("candidate") or {}


def popularity_limit(cfg: dict | None = None) -> int:
    c = cfg or load_candidate_config()
    return int(c.get("popularity_limit", 20))


def zt_min_boards(cfg: dict | None = None) -> int:
    c = cfg or load_candidate_config()
    return int(c.get("zt_min_boards", 3))


def cxg_labels(cfg: dict | None = None) -> tuple[str, ...]:
    c = cfg or load_candidate_config()
    raw = c.get("cxg_labels") or list(DEFAULT_CXG_LABELS)
    if isinstance(raw, (list, tuple)):
        return tuple(str(x).strip() for x in raw if str(x).strip())
    return DEFAULT_CXG_LABELS


def include_ths_rank_pool(cfg: dict | None = None) -> bool:
    c = cfg or load_candidate_config()
    return bool(c.get("include_ths_rank_pool", True))
