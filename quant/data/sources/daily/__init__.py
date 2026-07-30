"""日线库数据源（DailySource）实现包。

- ``_shared``：``_retry`` 退避 + 东财 kline 直连 helper（供 default/akshare 复用，断与 fetch.py 的循环依赖）。
- ``akshare``：``AkshareDailySource``（全 akshare，含 fetch_index 走 clist，不稳，作对照/回归）。
- ``default``：``DefaultDailySource``（组合最优：fetch_index 直连东财 kline，其余委托 akshare）。
"""
