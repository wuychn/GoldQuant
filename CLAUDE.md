# GoldQuant 开发约定

## 数据源：接口级换源是硬性原则

**facade 一个方法 = 一个接口 = 一个源，禁止在 facade 方法内部混源。**

- 四类协议（Daily/Market/Enrich/Info）的每个 `fetch_*` 方法，经 `InterfaceProxy` 按
  `quant.yml` 的 `data.sources.<类>.<接口>` 独立选源。
- 一个 facade 方法**只能走一个源**；业务层需要多源时自行组合多个 facade 方法。
- **源类只实现它支持的接口**，未实现的接口必须显式抛 `NotImplementedError`
  （提示可用哪个源），**禁止**隐式 fallback 到别的源、禁止漏实现（连方法都不定义）。
- 新增数据接口时：先加协议方法 → 各源类实现或抛 NotImplementedError → default 委托单源
  → 注册 registry → 补 quant.yml 注释。

架构文件：`quant/data/sources/interface.py`（InterfaceProxy）、`factory.py`、
`registry.py`、各 `daily/` `market/` `enrich/` `info/` 子包的源类。

## 分层

- 依赖方向硬约束：`common ← quant ← {app, scripts}`。`quant/` 不 import `app/`。
- `scripts/check_layering.py` 守卫；改分层后跑它。

## 其他

- 测试：改数据源/调度后跑 `python -m pytest quant/ scripts/data/ tests/`。
- 文档：新增脚本/配置后在 `docs/OPERATIONS.md`、`docs/CONFIG.md` 同步。
