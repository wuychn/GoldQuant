import json
from pathlib import Path

from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
from quant.scoring.theme_tracker import (
    _concept_tracker_cfg,
    _load_state,
    build_scoring_concept_pool,
    build_scoring_concept_scores,
    collect_concept_window_metrics,
)

p = sorted((Path.home() / ".quant/daily/2026-06-24/raw").glob("during_*.json"))[-1]
payload = json.loads(p.read_text(encoding="utf-8"))
st = _load_state()
cfg = _concept_tracker_cfg("during_market")
dw = cfg.get("dual_window") or {}
print("盘中双窗口:", dw.get("mode_overrides", {}).get("during_market", dw))
print("recency_decay:", cfg.get("recency_decay"))
print()

watch = [
    "创新药", "CRO概念", "重组蛋白", "钠离子电池", "培育钻石",
    "共封装光学(CPO)", "F5G概念", "芯片概念", "液冷服务器",
    "通信设备", "元件", "半导体", "消费电子", "光学光电子",
]

for section, label in [(BOARD_CONCEPT, "概念"), (BOARD_INDUSTRY, "行业")]:
    pool = build_scoring_concept_pool(payload, st, section=section)
    scores = build_scoring_concept_scores(payload, st, mode="during_market", section=section)
    metrics7 = collect_concept_window_metrics(st, payload, lookback=7, section=section)
    metrics3 = collect_concept_window_metrics(st, payload, lookback=3, section=section)
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    rank_map = {n: i + 1 for i, (n, _) in enumerate(ranked)}
    print(f"=== {label} 评分池 {len(pool)} 个 ===")
    for name in watch:
        if name not in pool and name not in scores:
            continue
        m7 = metrics7.get(name, {})
        m3 = metrics3.get(name, {})
        print(
            f"  {name}: 合成排名={rank_map.get(name, '-')}/{len(ranked)} "
            f"合成分={scores.get(name, 0):.1f} | "
            f"7日入选={m7.get('入选次数', 0):.1f} 涨幅={m7.get('综合涨幅', 0):.1f} | "
            f"3日入选={m3.get('入选次数', 0):.1f} 涨幅={m3.get('综合涨幅', 0):.1f}"
        )
    print("  合成Top10:", [(n, round(s, 1)) for n, s in ranked[:10]])
    print()
