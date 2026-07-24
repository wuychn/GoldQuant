"""机构风格因子：板块 + 个股。"""

from quant.factors.sector import compute_sector_factors, score_sector_row
from quant.factors.stock import compute_stock_alpha

__all__ = ["compute_sector_factors", "score_sector_row", "compute_stock_alpha"]
