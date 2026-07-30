"""资讯/基本信息数据源（InfoSource）实现包。

- ``default``：``DefaultInfoSource``（全局新闻 + 个股基本信息 jbxx）。

本包只做"取数"；新闻聚合后的 payload 封装、jbxx 周文件缓存等编排在调用方。
注：人气榜（hot）属 ``MarketSource.fetch_hot_raw``、问财概念（wencai）属
``EnrichSource.fetch_stock_concepts``，故不在此重复。
"""
