"""ML 离线校准 CLI（需手动执行，不会随 quant 五模式自动运行）。

示例::

    python -m quant.ml calibrate --method grid --dry-run
    python -m quant.ml calibrate --method linear --apply
"""

from __future__ import annotations

import argparse

from quant.ml.calibrate import calibrate, clear_scoring_cache, write_calibration


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GoldQuant ML 离线校准：用 ~/.quant/daily 历史优化评分阈值与维度权重",
    )
    parser.add_argument(
        "command",
        choices=["calibrate"],
        help="子命令（目前仅 calibrate）",
    )
    parser.add_argument(
        "--method",
        choices=["grid", "linear", "lightgbm", "bayesian"],
        default="grid",
        help="grid=网格阈值; linear=Ridge权重; lightgbm=特征重要性; bayesian=差分进化阈值",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=20,
        help="最少样本条数，不足则只提示不优化",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入 ~/.quant/config/ml_calibration.yml 并在下次 quant 运行时生效",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印结果，不写文件",
    )
    args = parser.parse_args()

    if args.command == "calibrate":
        result = calibrate(args.method, min_samples=args.min_samples)
        print(f"方法: {result.method}")
        print(f"样本数: {result.sample_count}")
        if result.thresholds:
            print("建议阈值:", result.thresholds)
        if result.dimension_weights:
            print("建议维度权重:", result.dimension_weights)
        if result.metrics:
            print("指标:", result.metrics)
        for note in result.notes:
            print("提示:", note)

        if args.dry_run:
            return
        if args.apply:
            path = write_calibration(result, apply=True)
            clear_scoring_cache()
            print(f"已写入 {path}，重启 quant 进程后生效。")
        else:
            print("未写入文件。确认后加 --apply 生效。")


if __name__ == "__main__":
    main()
