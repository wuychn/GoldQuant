"""从 6/24 晚间 optional_delta + 保留自选 + 6/25 合法新增，恢复 optional.jsonl。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from quant.constants import STRATEGY_NAME
from quant.store.state import save_optional

_QUANT = Path.home() / ".quant"

# 6/24 晚间 get_optional 仅 3 只（测试阶段读截断），merge 后 total=26 的另 3 只保留自选
_RETAINED_6_24 = {
    "603986": {
        "股票名称": "兆易创新",
        "加入自选原因": "兆易创新，所属行业半导体，同花顺人气榜第2，评分85",
        "最后入选日期": "2026-06-24",
    },
    "600522": {
        "股票名称": "中天科技",
        "加入自选原因": "中天科技，所属概念共封装光学(CPO)，同花顺人气榜第10，评分59",
        "最后入选日期": "2026-06-16",
    },
    "300308": {
        "股票名称": "中际旭创",
        "加入自选原因": "中际旭创，所属概念共封装光学(CPO)，评分52",
        "最后入选日期": "2026-06-18",
    },
}

_SCORES_6_24 = _QUANT / "daily/2026-06-24/derived/scores_watchlist.json"
_SCORES_6_25 = _QUANT / "daily/2026-06-25/derived/scores_watchlist.json"
_DELTA_6_24 = _QUANT / "daily/2026-06-24/derived/optional_delta.json"
_DELTA_6_25 = _QUANT / "daily/2026-06-25/derived/optional_delta.json"


def _load_scores(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for row in rows:
        code = str(row.get("股票代码", "")).strip()
        if code:
            out[code] = float(row.get("总分") or 0)
    return out


def _reason_score(reason: str, score: float) -> str:
    """同步原因文案末尾整数分与 ``评分`` 字段（四舍五入）。"""
    display = str(int(round(score)))
    if re.search(r"评分\d+", reason):
        return re.sub(r"评分\d+", f"评分{display}", reason)
    return f"{reason}，评分{display}" if reason else f"评分{display}"


def _build_row(
    code: str,
    *,
    base: dict,
    score: float,
    fail_streak: int = 0,
) -> dict:
    reason = _reason_score(str(base.get("加入自选原因") or ""), score)
    row = {
        "股票代码": code,
        "股票名称": base.get("股票名称", ""),
        "战法": STRATEGY_NAME,
        "评分": round(score, 2),
        "加入自选原因": reason,
        "最后入选日期": base.get("最后入选日期", "2026-06-24"),
        "未达标连续天数": fail_streak,
    }
    tags = base.get("榜单标签")
    if tags:
        row["榜单标签"] = tags
    return row


def main() -> None:
    delta_24 = json.loads(_DELTA_6_24.read_text(encoding="utf-8"))
    delta_25 = json.loads(_DELTA_6_25.read_text(encoding="utf-8"))
    scores_24 = _load_scores(_SCORES_6_24)
    scores_25 = _load_scores(_SCORES_6_25)
    threshold = 70.0

    by_code: dict[str, dict] = {}

    for row in delta_24.get("added") or []:
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue
        score = scores_25.get(code, scores_24.get(code, float(row.get("评分") or 0)))
        streak = 0
        if code in scores_25 and score < threshold:
            streak = 1
        by_code[code] = _build_row(code, base=row, score=score, fail_streak=streak)

    for code, meta in _RETAINED_6_24.items():
        score = scores_25.get(code, scores_24.get(code, 0.0))
        streak = 0
        if code in scores_25 and score < threshold:
            streak = 1
        elif code in scores_24 and score < threshold and code not in scores_25:
            streak = 1
        by_code[code] = _build_row(code, base=meta, score=score, fail_streak=streak)

    for row in delta_25.get("added") or []:
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue
        score = scores_25.get(code, float(row.get("评分") or 0))
        by_code[code] = _build_row(code, base=row, score=score, fail_streak=0)

    merged = list(by_code.values())
    merged.sort(
        key=lambda r: (
            -float(r.get("动能分") or 0),
            -float(r.get("评分") or 0),
            str(r.get("股票代码", "")),
        )
    )

    if len(merged) != 27:
        print(f"警告：预期 27 只（26+华天科技），实际 {len(merged)} 只")

    save_optional(
        merged,
        delta={
            "added": [],
            "removed": [],
            "restored": True,
            "source": "scripts/restore_optional.py",
            "count": len(merged),
        },
    )
    print(f"已恢复自选 {len(merged)} 只 → {_QUANT / 'state/optional.jsonl'}")
    for i, r in enumerate(merged[:5], 1):
        print(f"  {i}. {r['股票代码']} {r['股票名称']} 评分{r['评分']}")
    if len(merged) > 5:
        print(f"  ... 共 {len(merged)} 只")


if __name__ == "__main__":
    main()
