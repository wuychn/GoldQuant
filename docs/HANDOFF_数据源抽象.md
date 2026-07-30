# 数据源抽象 · 交接文档

> **状态**：①②③④ 全部完成并验证（四类协议 Daily/Market/Enrich/Info 抽象到位，旧 Spot/Index/News 已清理）。本文留作设计档案。
> 相关：全量方案见 `C:\Users\admin\.claude\plans\clever-crafting-mountain.md`；东财接口避坑见 memory `eastmoney-data-pitfalls.md`。

---

## 0. 一句话目标

让量化层"提供数据源规范"，后期接入不同数据源（tushare/sina/wind/自建）**只改 `quant.yml` 配置、不改代码**，akshare 退为一个可选实现。HTTP 层（`common/utils/source_headers.py` 的 curl_cffi + 策略）已可插拔；本任务把**数据源本身**也抽象到位。

复用 + 扩展现有 `quant/data/sources/` 的 **protocols + registry + factory** 模式（Phase 5 已建框架，本次扩展到全量）。

---

## 1. 总体架构（目标）

4 类协议，每类多实现（默认=组合最优源），factory 按 `quant.yml data.sources.{daily,market,enrich,info}` 选，调用方走 facade 零感知：

```
quant/data/sources/
  protocols.py     # DailySource / MarketSource / EnrichSource / InfoSource（旧 Spot/Index/News 已删）
  registry.py      # DAILY / MARKET / ENRICH / INFO 注册表（旧 SPOT/INDEX/NEWS 已删）
  factory.py       # get_daily_source / get_market_source / get_enrich_source / get_info_source
  daily/    __init__.py _shared.py akshare.py default.py        # ✅ ①
  market/   __init__.py default.py                              # ✅ ②
  enrich/   __init__.py default.py                              # ✅ ③
  info/     __init__.py default.py                              # ✅ ④
  # 原料（不动）：akshare/{fund_flow,index,news}、ths/{api,concept,funds,industry,concept_fit_rank}、
  #               eastmoney/{dfcf,industry}、etf52、fixture、rate_limit
  # 已删：akshare/spot.py、fixture/spot.py（旧 SpotSource 实现，语义并入 DailySource.fetch_spot）
```

---

## 2. 已完成（①②）—— 不要重做

### ① DailySource（日线库核心）✅
- `protocols.py`：`DailySource`（fetch_hist/fetch_index/fetch_calendar/fetch_code_list/fetch_delisted_codes/fetch_delisted_daily/fetch_spot）。
- `daily/_shared.py`：`_retry`（指数退避+限流加倍）+ `eastmoney_index_kline`（东财 push2his kline 直连，绕 akshare clist）。
- `daily/akshare.py`：`AkshareDailySource`（全 akshare，含归一化 `_normalize_hist/_normalize_index/_normalize_spot`）。
- `daily/default.py`：`DefaultDailySource`（**组合**：fetch_index 直连东财 kline，其余委托 AkshareDailySource）。
- `registry.py`：`DAILY_REGISTRY = {default, akshare}`。
- `factory.py`：`get_daily_source()`。
- `quant/data/fetch.py`：**改 facade**（6 函数委托 `get_daily_source()`），**不再 import akshare**。
- `quant/data/delist.py`：`_retry` 改从 `daily._shared` import。
- `quant/data/test_fetch_retry.py`：`_retry` import + monkeypatch 目标改 `daily._shared`。
- `quant.yml`：`data.sources.daily: default`。

### ② MarketSource（运维行情）✅
- `protocols.py`：`MarketSource`（fetch_index_spot/fetch_zqxy/fetch_ztgk_pool/fetch_ztgk_prev/fetch_hot_raw/fetch_concept_boards/fetch_industry_board/fetch_market_fund_flow）。
- `market/default.py`：`DefaultMarketSource`（组合：index_spot 经旧 IndexSource；zqxy 三级 fallback ths V2→52etf→ths；ztgk 东财；hot/concept/industry ths；fund_flow akshare）。
- `registry.py`：`MARKET_REGISTRY = {default}`；`factory.py`：`get_market_source()`。
- `quant/data/tools/market.py`：**改 facade**（7 fetch_* 委托 `get_market_source()`），保留业务（`ztgk_rows`/`_apply_row_limit`/`prefilter_popularity`/`unpack_concept_boards`）；`ztgk_rows` 改签名接收 `zrzt`（由 facade 经 source.fetch_ztgk_prev 取）。
- `quant.yml`：`data.sources.market: default`。

### ①② 验证状态
- `get_daily_source().name == "default"`、`get_market_source().name == "default"`。
- `fetch_hist('000001')`/`fetch_index('000300')` 经 facade 各拉 8 行成功。
- `grep akshare quant/data/fetch.py` 为空（仅 docstring 文字）。
- **全量 pytest：213 passed, 6 failed（全是 pre-existing，见 §8.6）**。

### factory 关键细节（③④ 照搬）
`_source_name(key, *, fixture_default="fixture", default="akshare")`：
- 新协议（daily/market/enrich/info）调用时传 `default="default"`（registry 才有 default）。
- 旧协议（spot/index/news）保持 `default="akshare"`。

---

## 3. ④ InfoSource + 旧协议清理 + 文档（已完成）

### ③ EnrichSource（个股 enrich 取数）✅ 已完成（2026-07-30）
**目标**：把 `enrich_stock_row` / `attach_stock_concepts` 里"直接调源取数"的部分抽成 `EnrichSource`，业务编排（concept 三级 cache fallback、io_tasks 并发、归一化、jbxx/concept cache、archive）保留在 `services/enrich.py`。**深入重构**（用户选定）：attach_stock_concepts 的 wencai/ths fallback 取数也纳入 source。

**实现**：
- `protocols.py`：`EnrichSource`（fetch_stock_quote/fetch_stock_fund_flow/fetch_stock_fund_flow_daily/fetch_concept_fit_rank/fetch_stock_concepts/fetch_stock_minute）。
  - **设计偏差（已定）**：原设计把"wencai wcxg + ths concept_fit_rank fallback"都塞进一个 `fetch_stock_concepts`。实测分离成两个方法（`fetch_concept_fit_rank` ths + `fetch_stock_concepts` wencai），才能保留原两级 cache（粘合度/概念）与 `概念粘合度` 富数据、不破坏 `enrich_stock_rows` 输出。
- `enrich/default.py`：`DefaultEnrichSource`（盘口 pk / 资金流 ggzjl+万元归一化+ThsFundsFetchError / 资金流日线 zj / 概念粘合度 get_concept_fit_rank / 问财 wcxg / 分钟K akshare）。
  - 取数+单点错误兜底整体搬入（`_ggzjl`/`_fund_flow_daily`/`_sync_call_or_none("盘口",pk)`/`stock_intraday_minute_zh` 整体搬）；概念两方法只搬原始拉取（错误仍由 enrich 的 cache wrapper 兜底）。
- `services/market_enrich.py`：**已删除**（`stock_intraday_minute_zh`/`pre_auction_minute_zh` 仅 enrich.py 用；搬到 source 后模块空，删；解决分层 services→sources 反向）。
- `registry.py`：`ENRICH_REGISTRY = {default}`；`factory.py`：`get_enrich_source()`（`default="default"`）。
- `services/enrich.py`：改 facade（io_tasks 4+1 委托 `get_enrich_source().fetch_*()`；`fetch_stock_concept_fit_ths`/`fetch_stock_concepts_wcxg` 的取数行委托 source；删 `_ggzjl`/`_fund_flow_daily`；保留 `_load_hist`(archive)/`fetch_jbxx_cached`/`fetch_stock_industry`(cache)/concept cache 编排）。
- `quant.yml`：`data.sources.enrich: default`。

### ③ 验证状态
- `get_enrich_source().name == "default"`、`isinstance(src, EnrichSource)` True、6 方法齐备。
- io_tasks 字段映射端到端验证（fake source）：盘口/个股资金流/个股资金流日线/分钟行情 索引正确。
- `python -m scripts.check_layering` 通过；`quant/yml_schema` 通过。
- `grep akshare services/enrich.py` 为空（akshare 仅在 `sources/enrich/default.py` 内 akshare 调用）。
- **全量 pytest：213 passed, 6 failed（全是 pre-existing，无回归）**。

### ④ InfoSource + 旧协议清理 + 文档 ✅ 已完成（2026-07-30）
**目标**：资讯/基本信息抽 `InfoSource`；`jbxx_cache` 取数委托 source；删旧 `SpotSource/IndexSource/NewsSource` + `get_spot/index/news_source`；补文档。

**实现**：
- `protocols.py`：`InfoSource`（fetch_news / fetch_stock_info）。
  - **设计偏差（已定）**：原设计 InfoSource 含 `fetch_stock_news`/`fetch_hot_rank`/`wencai_query`。实测 `hot` 已在 `MarketSource.fetch_hot_raw`、`wencai` 已在 `EnrichSource.fetch_stock_concepts`，重复入 Info 会协议重叠 → InfoSource 只留**不重叠**的 news + 个股基本信息（jbxx，命名 `fetch_stock_info` 而非 `fetch_stock_news`，因 jbxx 是基本信息非资讯）。
- `info/default.py`：`DefaultInfoSource`（fetch_news fixture-aware：fixture 模式 `FixtureNewsSource.fetch_global` else `AkshareNewsSource.fetch_global`；fetch_stock_info = 东财 `jbxx`）。
- `registry.py`：`INFO_REGISTRY = {default}`；`factory.py`：`get_info_source()`；`quant.yml`：`data.sources.info: default`。
- **调用方委托**：`payload.build_news_payload` → `get_info_source().fetch_news()`；`jbxx_cache._fetch_jbxx_live` → `get_info_source().fetch_stock_info()`（去掉直接 `eastmoney.jbxx` import）。
- **旧协议清理**：
  - 删 `protocols.py` 的 `SpotSource/IndexSource/NewsSource`；`registry.py` 的 `SPOT/INDEX/NEWS_REGISTRY` + 三个 `from_registry` + 6 个旧类 import；`factory.py` 的 `get_spot/index/news_source` + 旧协议 import；`sources/__init__.py` 旧导出。
  - `factory._source_name` 简化为 `_source_name(key)`（fixture/未配置均 `default`；去掉 `default="akshare"` 旧坑）。
  - `DefaultMarketSource.fetch_index_spot` **去 `get_index_source()` 依赖**：改 fixture-aware（fixture→`FixtureIndexSource` else `AkshareIndexSource`），保留旧行为。
  - 删 dead 实现 `akshare/spot.py`、`fixture/spot.py`（语义并入 `DailySource.fetch_spot`，无调用方）+ 清两 `__init__`。
  - `tests/sources/test_source_conformance.py` 改为测四类协议（get_*_source isinstance），删旧 spot/index 用例。
- **文档**：`docs/CONFIG.md`（`data` 段拆"限流"+"数据源选择"两表，列四类 sources）+ `docs/ARCHITECTURE.md`（§5.1 数据源抽象小节 + 表）。

### ④ 验证状态
- `get_info_source().name == "default"`、`isinstance(src, InfoSource)` True。
- `build_news_payload`/`jbxx_cache._fetch_jbxx_live` 委托验证（fake source 路由正确）。
- 无代码残留引用旧 `SpotSource/IndexSource/NewsSource`/`get_spot·index·news_source`/旧 REGISTRY。
- `python -m scripts.check_layering` 通过；`quant/yml_schema` 通过；`grep akshare` 各 facade 为空（仅 fetch.py docstring 文字）。
- **全量 pytest：6 failed（全是 pre-existing，与 ①②③ 完全一致，无回归）**。

---

## 4. ③ EnrichSource 详细设计

### 协议（`protocols.py` 追加）
```python
@runtime_checkable
class EnrichSource(Protocol):
    name: str
    def fetch_stock_quote(self, symbol: str) -> Any: ...                  # 盘口 pk（东财）
    def fetch_stock_fund_flow(self, symbol: str) -> Any: ...              # 个股资金流（ths ggzjl，含归一化）
    def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> Any: ...  # 资金流日线（东财 zj）
    def fetch_stock_concepts(self, symbol: str) -> Any: ...               # 概念（wencai wcxg + ths concept_fit_rank fallback）
    def fetch_stock_minute(self, symbol: str, *, context: str = "") -> Any: ...  # 分钟K（akshare stock_zh_a_hist_pre_min_em）
```

### DefaultEnrichSource（`enrich/default.py`，搬现有取数）
对照 `services/enrich.py` 现状搬：
| 方法 | 搬自 | 原料 |
|---|---|---|
| fetch_stock_quote | `enrich_stock_row:521` 的 `pk(symbol)` | `quant.data.sources.eastmoney.pk` |
| fetch_stock_fund_flow | `_ggzjl`（407-428）整体（含 list_to_dict_v2 + 万元归一化 + ThsFundsFetchError 处理） | `quant.data.sources.ths.ggzjl` |
| fetch_stock_fund_flow_daily | `_fund_flow_daily`（431-441） | `quant.data.sources.eastmoney.zj` |
| fetch_stock_concepts | `attach_stock_concepts`（244-313）的**取数部分**（wencai `fetch_stock_concepts_wcxg` 357 + ths `fetch_stock_concept_fit_ths` 178 fallback） | `quant.data.sources.ths` |
| fetch_stock_minute | `services/market_enrich.stock_intraday_minute_zh`（**搬到 sources**，见坑） | akshare |

### services/enrich.py 改造（facade + 编排保留）
- `enrich_stock_row`（479-557）的 `io_tasks`（520-533）：`pk/_ggzjl/_fund_flow_daily/stock_intraday_minute_zh` → `get_enrich_source().fetch_*()`；`_load_hist`/`load_computed_metrics_zh`（archive）、`fetch_jbxx_cached`/`fetch_stock_industry`（cache）**保留**（archive/cache 层）。
- `attach_stock_concepts`：**分离**——cache 查/写逻辑保留在 enrich；取数（wencai/ths fallback）调 `source.fetch_stock_concepts`。注意它有 `api_allowed` 开关（cache-only 模式）。
- registry/factory 加 ENRICH（`default="default"`）；`quant.yml` 加 `data.sources.enrich: default`。

### ③ 必读坑
- **先读** `services/enrich.py` 全文（尤其 `attach_stock_concepts` 244-313、`fetch_stock_concepts_wcxg` 357-407、`_load_hist` 126-150、`enrich_stock_row` 479-557）再动手——业务和取数交织，分离要小心。
- **market_enrich.stock_intraday_minute_zh 要搬到 sources**（如 `sources/akshare/minute.py` 或 EnrichSource 内 akshare 调用）：因为它在 `services/`，`sources/enrich/` 不能 import services（分层：sources < services）。搬时连同 `pre_auction_minute_zh`。
- **archive（_load_hist/load_computed_metrics_zh）不进 EnrichSource**：它们是离线库/cache 层（已用 DailySource）；enrich.py 继续直接调 archive。
- **jbxx_cache/concept_cache 不进 EnrichSource**：是 cache 层；但其**内部取数**（`_fetch_jbxx_live`/wencai）可委托 EnrichSource/InfoSource（④做）。
- `_ggzjl` 的 `ThsFundsFetchError` 异常处理要随取数搬入 DefaultEnrichSource（保留 http_status 提取）。

---

## 5. ④ InfoSource 详细设计

### 协议（`protocols.py` 追加）
```python
@runtime_checkable
class InfoSource(Protocol):
    name: str
    def fetch_news(self) -> Any: ...                    # 新闻（旧 NewsSource.fetch_global 语义）
    def fetch_stock_news(self, symbol: str) -> Any: ...  # 个股资讯（东财 dfcf）
    def fetch_hot_rank(self, limit: int) -> Any: ...     # 人气榜（ths hot_stock）
    def wencai_query(self, question: str) -> Any: ...    # 问财（ths wcxg）
```

### DefaultInfoSource（`info/default.py`）
- fetch_news：复用旧 `AkshareNewsSource.fetch_global`（`sources/akshare/news.py`）。
- fetch_stock_news：`sources/eastmoney/dfcf`（`_fetch_stock_individual_info_em`/`jbxx`）。
- fetch_hot_rank：`sources/ths/api.hot_stock`。
- wencai_query：`sources/ths/api.wcxg`。

### 调用方改造
- `payload.py` 的 news（`build_news_payload` 用 `get_news_source`）→ `get_info_source().fetch_news()`。
- `concept_cache`/`jbxx_cache` 的取数（wencai/jbxx live）→ 委托 EnrichSource/InfoSource。

### 旧协议清理（④末尾，迁移完成后）
- 删 `protocols.py` 的 `SpotSource/IndexSource/NewsSource`（语义已并入 Daily/Market/Info）。
- 删 `registry.py` 的 `SPOT/INDEX/NEWS_REGISTRY` + `get_spot/index/news_source_from_registry`。
- 删 `factory.py` 的 `get_spot_source/get_index_source/get_news_source`（确认无调用方后；`tools/market.py` 的 `fetch_index_spot` 已迁到 `get_market_source`，但 `DefaultMarketSource.fetch_index_spot` 内部仍 `get_index_source()`——要改成直接调东财原料或保留 IndexSource 过渡）。
- **注意**：`fixture` 实现可能引用旧协议，清理时一并处理。

---

## 6. 继续步骤（建议顺序）

每步独立可验证、可提交：
1. ③-1：读 `services/enrich.py` 全文 + `market_enrich.py`，列清取数 vs 业务边界。
2. ③-2：`market_enrich.stock_intraday_minute_zh` 搬到 `sources/`（解决分层）。
3. ③-3：`protocols.py` 加 `EnrichSource`；`enrich/default.py`（搬取数）；registry/factory；`quant.yml`。
4. ③-4：`services/enrich.py` 改 facade（io_tasks + attach_stock_concepts 委托 source，编排保留）。
5. ③-5：验证（见 §7）→ 提交。
6. ④-1：`protocols.py` 加 `InfoSource`；`info/default.py`；registry/factory；`quant.yml`。
7. ④-2：资讯调用方 + cache 委托。
8. ④-3：删旧 Spot/Index/News 协议 + factory getter（确认无引用）。
9. ④-4：文档（`docs/CONFIG.md` 数据源段 4 类 + 配置；`docs/ARCHITECTURE.md` 数据层图）。
10. ④-5：全量验证 → 提交。

---

## 7. 验收清单（每阶段）

- **行为不变**：全量 `pytest app/ quant/ scripts/` = **213 passed, 6 failed**（6 全是 pre-existing，见 §8.6；不能多）。
- **akshare 可替换**：`grep akshare <facade文件>` 为空（`fetch.py`/`tools/market.py`/`services/enrich.py`/资讯调用方）；akshare 只在 `sources/akshare/*` 和各 default 实现内。
- **配置切换**：`quant.yml` 改 `data.sources.enrich/info` → `get_enrich_source().name`/`get_info_source().name` 变化。
- **分层守卫**：`python -m scripts.check_layering` 通过（common 无 app/quant；quant 无 app）。
- **端到端**：
  - `python -m scripts.data.build_daily --limit 5`（①）。
  - `QUANT_USE_LOCAL_FIXTURE=true python -m quant during_market`（②③④ payload）。
  - `enrich_stock_rows` 对 fixture 输出不变（③）。

---

## 8. 必读约束 / 坑（新会话开工前先看）

### 8.1 HTTP 层已就绪（不要重做）
`common/utils/source_headers.py`：策略模式 + **curl_cffi 统一层**。`apply_source_header_patch()` 在 `quant/data/fetch.py` 模块级调用，hook `requests.Session.request` → 所有 requests 系请求（akshare/东财/llm_client）走 `curl_cffi impersonate="chrome"`（解决东财 clist 的 TLS 指纹反爬），异常回退 requests。东财策略注入 `.eastmoney.header`（Cookie/UA）+ 可配限速 `set_eastmoney_interval`。**③④ 新 source 调 requests 系原料时自动受益，无需额外处理。**

### 8.2 同花顺保持 httpx（不要换 curl_cffi）
`ths/`（api/concept/funds/industry）用 **httpx + Hexin-V**（`iwencai_hexin_util` 生成）。用户已定：保持 httpx，不换 curl_cffi（同花顺没 TLS 问题）。③④ 的 ths 取数（ggzjl/wcxg/hot/concept）走 httpx 原料即可。

### 8.3 分层硬约束
`common ← quant ← {app, scripts}`。`scripts/check_layering.py` 守卫：**quant 不引 app、common 不引 app/quant**。③ 把 `market_enrich` 搬 sources 时注意（services 不能被 sources import）。

### 8.4 factory 默认值
新协议（enrich/info）的 `get_*_source` 必须传 `default="default"`（registry 只有 default）；否则 `_source_name` 默认 "akshare" → registry 报"未知 source"（②踩过这个坑）。

### 8.5 东财接口避坑（memory `eastmoney-data-pitfalls`）
- **clist**（push2 实时列表）：TLS 指纹反爬，**必须 curl_cffi**（已统一）。
- **kline**（push2his 历史）：稳，东财直连首选（`fetch_index` 已用）。
- **stockapi**（代码表）：稳（`fetch_code_list` 已用）。
- `.eastmoney.header` 的 Cookie 有时效，失效更新（`POST /api/v1/admin/eastmoney`）；该文件**已从 git 移除 + gitignore**。

### 8.6 测试 baseline（不要误判为回归）
全量 **213 passed, 6 failed**，6 个全是 **pre-existing**（与数据源抽象无关）：
- `test_paper_execute::test_execute_decision_card_writes_paper_account`（flaky，依赖纸面账户状态文件）
- `test_factors::test_factors_compute_on_synthetic`（数值断言）
- `test_r3_fixes` ×3（`panel_builder.py:150` 对 frozen dataclass 赋值 → `FrozenInstanceError`）
- `test_refactor::test_build_pre_market_payload_mocked`（mock 目标写错：patch enrich 模块但 payload import 时绑定）

环境：全局 python `D:\ProgramFiles\python311`（无 .venv）；akshare/curl_cffi/py_mini_racer 已装全局。

---

## 9. 本次（①②）已改/新增文件清单

**新增**：`sources/daily/{__init__,_shared,akshare,default}.py`、`sources/market/{__init__,default}.py`、`docs/HANDOFF_数据源抽象.md`（本文）。
**改动**：`sources/protocols.py`（+DailySource/MarketSource）、`sources/registry.py`（+DAILY/MARKET）、`sources/factory.py`（+get_daily/market_source, _source_name 加 default 参数）、`quant/data/fetch.py`（facade）、`quant/data/delist.py`（_retry 来源）、`quant/data/test_fetch_retry.py`（import+monkeypatch）、`quant/data/tools/market.py`（facade）、`quant/config/quant.yml`（data.sources.daily/market）。

**未提交**：①② 改动在工作区未 commit（用户可决定提交时机）。
