"""个股 enrich 数据源（EnrichSource）实现包。

- ``default``：``DefaultEnrichSource``（盘口/资金流/概念/分钟K 的原子取数，组合各厂商最稳后端）。

业务编排（concept 三级 cache fallback、io_tasks 并发、jbxx/concept cache、archive 读写）
留在 ``quant/services/enrich.py`` facade；本包只做"取数"。
"""
