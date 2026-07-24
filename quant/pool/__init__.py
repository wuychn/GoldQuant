"""候选池：三来源初筛 + 通用 enrich/概念漏斗 + 晚间合并。"""

from quant.pool.candidate_config import (
    PAYLOAD_KEY_CXFL,
    PAYLOAD_KEY_CXG,
    PAYLOAD_KEY_LJQS,
    PAYLOAD_KEY_LXSZ,
    PAYLOAD_KEY_POPULARITY,
    PAYLOAD_KEY_ZT,
    THS_RANK_PAYLOAD_KEYS,
)

__all__ = [
    "PAYLOAD_KEY_CXFL",
    "PAYLOAD_KEY_CXG",
    "PAYLOAD_KEY_LJQS",
    "PAYLOAD_KEY_LXSZ",
    "PAYLOAD_KEY_POPULARITY",
    "PAYLOAD_KEY_ZT",
    "THS_RANK_PAYLOAD_KEYS",
    "build_all_source_candidates",
    "build_candidates",
    "build_popularity_candidates",
    "build_ths_rank_candidates",
    "build_zt_candidates",
    "run_candidate_pipeline",
]


def __getattr__(name: str):
    if name == "build_candidates":
        from quant.pool.builder import build_candidates

        return build_candidates
    if name == "run_candidate_pipeline":
        from quant.pool.pipeline import run_candidate_pipeline

        return run_candidate_pipeline
    if name in {
        "build_all_source_candidates",
        "build_popularity_candidates",
        "build_ths_rank_candidates",
        "build_zt_candidates",
    }:
        from quant.pool import candidate_sources

        return getattr(candidate_sources, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
