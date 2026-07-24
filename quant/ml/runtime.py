"""R2 ML 运行时打分。"""

from __future__ import annotations

from quant.config import load_r2_config
from quant.ml.features import stock_feature_vector
from quant.store.paths import quant_home


def _load_model(name: str):
    try:
        import joblib
    except ImportError:
        return None
    except OSError:
        return None
    path = quant_home() / "ml" / "models" / name
    if not path.is_file():
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


def score_entry(stock: dict, payload: dict, *, regime: str = "") -> float:
    """返回盈利概率 0～1；无模型时回退结构分/100。"""
    cfg = load_r2_config().get("ml") or {}
    model_name = str(cfg.get("model_stock_rank") or "stock_rank.lgb")
    bundle = _load_model(model_name)
    if not bundle:
        from quant.signals.structure import structure_score

        return max(0.0, min(1.0, structure_score(stock) / 100.0))

    model = bundle["model"]
    cols = bundle["features"]
    feats = stock_feature_vector(stock, payload, regime=regime)
    row = [float(feats.get(c, 0.0)) for c in cols]
    try:
        if hasattr(model, "predict_proba"):
            prob = float(model.predict_proba([row])[0][1])
        else:
            prob = float(model.predict([row])[0])
    except Exception:
        from quant.signals.structure import structure_score

        prob = structure_score(stock) / 100.0
    return max(0.0, min(1.0, prob))


def write_shadow_scores(scores: list[dict], *, date_str: str) -> None:
    from quant.store.snapshot import save_derived

    save_derived("ml_scores.json", {"date": date_str, "scores": scores})
