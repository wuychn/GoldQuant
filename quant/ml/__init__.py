"""ML 离线校准包。

手动运行::

    python -m quant.ml calibrate --method grid --apply

详见 quant/ml/calibrate.py、quant/ml/dataset.py、quant/ml/optimizers.py。
"""

from quant.ml.calibrate import CalibrationResult, calibrate, load_merged_scoring_config, write_calibration

__all__ = ["CalibrationResult", "calibrate", "load_merged_scoring_config", "write_calibration"]
