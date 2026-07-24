"""实验注册表：可复现、可对比。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from quant.config import load_quant_config
from quant.store.paths import QUANT_HOME, ensure_layout
from quant.timeutil import CN_TZ


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()[:12]
    except Exception:
        return "unknown"


def _config_hash(cfg: dict) -> str:
    raw = yaml.dump(cfg, allow_unicode=True, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class ExperimentRun:
    id: str
    hypothesis: str
    git_sha: str
    config_hash: str
    data_from: str = ""
    data_to: str = ""
    mode: str = "full_system"
    created_at: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def artifacts_dir(self) -> Path:
        return QUANT_HOME / "experiments" / self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_experiment(
    *,
    hypothesis: str,
    data_from: str = "",
    data_to: str = "",
    mode: str = "full_system",
) -> ExperimentRun:
    ensure_layout()
    exp_id = datetime.now(CN_TZ).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    cfg = load_quant_config()
    return ExperimentRun(
        id=exp_id,
        hypothesis=hypothesis,
        git_sha=_git_sha(),
        config_hash=_config_hash(cfg),
        data_from=data_from,
        data_to=data_to,
        mode=mode,
        created_at=datetime.now(CN_TZ).isoformat(),
    )


def save_experiment(
    exp: ExperimentRun,
    *,
    metrics: dict[str, Any] | None = None,
    extra_files: dict[str, Any] | None = None,
) -> Path:
    """保存实验产物到 ~/.quant/experiments/{id}/。"""
    ensure_layout()
    root = exp.artifacts_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)

    if metrics:
        exp.metrics = metrics
    (root / "metrics.json").write_text(
        json.dumps(metrics or exp.metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "experiment.json").write_text(
        json.dumps(exp.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    merged_cfg = load_quant_config()
    (root / "config" / "quant_merged.yml").write_text(
        yaml.dump(merged_cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    if extra_files:
        for name, content in extra_files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, (dict, list)):
                path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                path.write_text(str(content), encoding="utf-8")
    return root
