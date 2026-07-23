"""R2 ML 训练。"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from quant.r2.ml.labels import attach_labels
from quant.store.paths import quant_home


FEATURE_COLS = [
    "structure_score",
    "in_main_wave",
    "day_chg",
    "last_price",
]


def _load_stock_df() -> pd.DataFrame:
    out_dir = quant_home() / "ml" / "features"
    pq = out_dir / "stock_daily.parquet"
    csv = out_dir / "stock_daily.csv"
    if pq.is_file():
        df = pd.read_parquet(pq)
    elif csv.is_file():
        df = pd.read_csv(csv)
    else:
        raise FileNotFoundError(f"缺少特征文件，请先运行 python -m quant r2_ml_etl")
    return attach_labels(df)


def train_stock_rank(*, test_ratio: float = 0.2) -> dict:
    try:
        import joblib
    except ImportError:
        return {"ok": False, "note": "请安装 ML 依赖: pip install -e '.[ml]'"}
    df = _load_stock_df()
    for c in FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0.0
    df = df.dropna(subset=FEATURE_COLS)
    if df.empty or len(df) < 20:
        return {"ok": False, "note": f"样本不足: {len(df)}"}

    df = df.sort_values("date")
    split = int(len(df) * (1 - test_ratio))
    train, test = df.iloc[:split], df.iloc[split:]
    X_train = train[FEATURE_COLS].astype(float).values
    y_train = train["label"].astype(float).values
    X_test = test[FEATURE_COLS].astype(float).values
    y_test = test["label"].astype(float).values

    try:
        import lightgbm as lgb

        model = lgb.LGBMClassifier(
            n_estimators=60,
            max_depth=4,
            learning_rate=0.08,
            verbose=-1,
        )
        model.fit(X_train, y_train)
        prob = model.predict_proba(X_test)[:, 1] if len(X_test) else np.array([])
    except ImportError:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=500)
        model.fit(X_train, y_train)
        prob = model.predict_proba(X_test)[:, 1] if len(X_test) else np.array([])

    acc = float(((prob >= 0.5).astype(int) == y_test).mean()) if len(y_test) else 0.0
    out_dir = quant_home() / "ml" / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "stock_rank.lgb"
    joblib.dump({"model": model, "features": FEATURE_COLS}, model_path)

    meta = {
        "ok": True,
        "task": "stock_rank",
        "train_n": len(train),
        "test_n": len(test),
        "test_acc": round(acc, 4),
        "model_path": str(model_path),
    }
    (out_dir / "stock_rank.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def train_sector_fade(*, test_ratio: float = 0.2) -> dict:
    try:
        import joblib
    except ImportError:
        return {"ok": False, "note": "请安装 ML 依赖: pip install -e '.[ml]'"}
    out_dir = quant_home() / "ml" / "features"
    pq = out_dir / "sector_daily.parquet"
    csv = out_dir / "sector_daily.csv"
    if pq.is_file():
        df = pd.read_parquet(pq)
    elif csv.is_file():
        df = pd.read_csv(csv)
    else:
        return {"ok": False, "note": "缺少 sector 特征文件"}
    if df.empty or len(df) < 20:
        return {"ok": False, "note": f"sector 样本不足: {len(df)}"}

    df = df.sort_values(["sector", "date"])
    df["label_fade"] = (df["lifecycle"] == "退潮").astype(int)
    cols = ["change_pct", "net_flow", "rank_gain", "rank_fund", "defensive", "eligible"]
    for c in cols:
        if c not in df.columns:
            df[c] = 0
    df = df.dropna(subset=["change_pct"])

    split = int(len(df) * (1 - test_ratio))
    train, test = df.iloc[:split], df.iloc[split:]
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(max_iter=500)
    model.fit(train[cols].astype(float).fillna(0), train["label_fade"])
    acc = float(model.score(test[cols].astype(float).fillna(0), test["label_fade"])) if len(test) else 0.0

    out_dir = quant_home() / "ml" / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "sector_fade.lgb"
    joblib.dump({"model": model, "features": cols}, model_path)
    return {"ok": True, "task": "sector_fade", "test_acc": round(acc, 4), "model_path": str(model_path)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=("stock_rank", "sector_fade", "all"), default="all")
    args = ap.parse_args()
    if args.task in ("stock_rank", "all"):
        print(train_stock_rank())
    if args.task in ("sector_fade", "all"):
        print(train_sector_fade())


if __name__ == "__main__":
    main()
