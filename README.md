# GoldQuant

GoldQuant 是一个 A 股短线辅助决策系统，当前目标是：聚合行情与榜单数据，使用确定性规则引擎生成可复现的结构化信号，并推送到飞书，由人工执行交易。

> **说明**：本服务仅做数据聚合与转发，不构成投资建议。行情与榜单数据来自第三方网站，存在延迟、字段变更或访问失败的可能。

---

## 项目结构

```text
GoldQuant/
├── app/                         # FastAPI 数据服务层
│   ├── main.py                  # `create_app()` 与全局 `app` 实例
│   ├── __main__.py              # `python -m app` 启动 Uvicorn
│   ├── api/
│   │   ├── deps.py              # 依赖注入
│   │   └── v1/
│   │       ├── router.py        # 聚合 v1 路由
│   │       └── endpoints/       # 行情、资金、板块、新闻、量化聚合接口
│   ├── core/
│   │   ├── config.py            # `pydantic-settings` 配置与 `.env`
│   │   └── proxy.py             # 出站代理配置
│   └── utils/                   # 数据清洗、归档、东财/同花顺工具
├── quant/                       # 辅助决策与量化规则层
│   ├── __main__.py              # `python -m quant` 统一入口
│   ├── cli.py                   # run / signal / replay 命令
│   ├── pipeline.py              # 规则引擎流水线：取数、信号、状态更新、飞书
│   ├── data_source.py           # local/remote 数据源加载
│   ├── feishu.py                # 飞书推送
│   ├── config.py                # `quant/strategy.yml` 读取
│   ├── features.py              # 特征提取
│   ├── market_state.py          # 市场状态判断
│   ├── signals.py               # 结构化信号生成
│   ├── replay.py                # 本地样例回放
│   ├── rules/                   # 涨停板、龙回头、风控规则
│   ├── data/                    # 本地样例数据
│   ├── strategy.md              # 人类可读策略说明
│   └── strategy.yml             # 机器可读策略配置，含参数注释
├── run.sh                       # Linux 后台启动 FastAPI 数据服务
├── pyproject.toml               # 包元数据、依赖与 `goldquant` 命令
├── requirements.txt             # 传统依赖安装入口
└── README.md
```

---

## 职责分层

`app/` 只负责数据服务。它通过 FastAPI 聚合 AKShare、东方财富、同花顺等公开数据源，并提供 `/api/v1/...` HTTP 接口。实时模式下，`quant` 会调用这些接口取数。

`quant/` 负责辅助决策。它读取本地样例或实时 API 数据，执行确定性规则、生成结构化信号，最后更新 `~/.quant` 状态文件并推送飞书。

`~/.quant` 是运行态目录，存放自选、持仓、资金、止损、信号与复盘归档；`quant/data` 是仓库内的本地样例数据，不应当写入运行状态。

---

## 配置说明

- 配置类位于 `app/core/config.py`，通过 **`pydantic-settings`** 读取环境变量，**前缀为 `GOLDQUANT_`**（例如 `GOLDQUANT_ENV`）。
- 项目根目录下的 **`.env`** 使用**固定绝对路径**加载（相对 `app/core/config.py` 解析），与从哪个目录启动无关；勿提交仓库（已列入 `.gitignore`），字段说明见 **`.env.example`**。
- 常用项：`GOLDQUANT_CORS_ORIGINS`（跨域源，`*` 或逗号分隔）、`GOLDQUANT_HTTP_CLIENT_TIMEOUT`、`GOLDQUANT_THS_DEFAULT_USER_AGENT` 等。
- 飞书凭据通过 `.env` 配置：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_USER_ID`。也可使用对应的 `GOLDQUANT_` 前缀变量。
- **出站代理**：`GOLDQUANT_PROXY_ENABLED`、`GOLDQUANT_PROXY_URL`（或分别设置 `GOLDQUANT_PROXY_HTTP_URL` / `GOLDQUANT_PROXY_HTTPS_URL`）、`GOLDQUANT_PROXY_NO_PROXY`。开启后会在进程内设置 `HTTP_PROXY`/`HTTPS_PROXY`，AKShare 与本服务中的 httpx 请求均会走代理。

---

## 环境要求

- Python **3.10+**（当前开发环境为 3.11，已在 Windows 验证）
- 可访问外网（拉取东方财富、雪球、同花顺等数据源）

---

## 使用 venv 管理依赖

在项目根目录 `GoldQuant` 下执行（**PowerShell**）：

```powershell
# 若尚未创建虚拟环境
python -m venv .venv

# 激活（Windows PowerShell）
.\.venv\Scripts\Activate.ps1

# 安装依赖（二选一）
pip install -r requirements.txt
# 或从 pyproject 以可编辑方式安装本项目包
# pip install -e .
```

CMD 用户可使用 `.\.venv\Scripts\activate.bat`。

复制环境变量模板（可选）：

```powershell
copy .env.example .env
```

---

## 启动数据服务

**必须在项目根目录 `GoldQuant` 下执行**（保证能 `import app`），并已激活 venv。

推荐任选其一：

```powershell
# 方式 A：模块入口（会读取 .env 中的 HOST/PORT/RELOAD，并打印监听地址）
python -m app

# 方式 B：与官方文档一致的 Uvicorn 命令行
uvicorn app.main:app --reload --host 0.0.0.0 --port 8085
```

**Linux 后台启动 / 停止 / 重启**（项目根目录 `run.sh`，使用 `nohup`，**无热重载**，适合服务器常驻）：

```bash
chmod +x run.sh
./run.sh start    # 写入 goldquant.pid，日志追加到 logs/goldquant.log
./run.sh stop     # 按 PID 优雅停止，必要时 SIGKILL
./run.sh restart  # 先 stop（若曾用本脚本启动），再 start
```

首次使用前需已创建 `.venv` 并安装依赖。若存在 `.env`，启动前会读取 `GOLDQUANT_HOST`、`GOLDQUANT_PORT` 等变量（默认 `0.0.0.0:8085`）。

- 交互式 API 文档：<http://127.0.0.1:8085/docs>
- OpenAPI JSON：<http://127.0.0.1:8085/openapi.json>
- 健康检查：<http://127.0.0.1:8085/health>

### 启动后立刻退出、且几乎没有输出？

常见原因是 **只运行了 `app/main.py` 文件**（例如在 IDE 里用「运行当前文件」打开 `app/main.py`）。该文件只**定义** FastAPI 应用对象，**不会**调用 Uvicorn，进程会正常结束（退出码 0），看起来像「闪退」。

请改用上面的 **`python -m app`** 或 **`uvicorn app.main:app ...`**，并确认**工作目录**为项目根目录。若仍异常，在 PowerShell 中执行 `cd d:\workspace\GoldQuant`（换成你的路径）后再启动。

监听地址与端口可通过环境变量 **`GOLDQUANT_HOST` / `GOLDQUANT_PORT`**（见 `.env.example`）调整。

### `.env` 里配置了 `GOLDQUANT_PORT` 仍不生效？

1. **配置加载位置**：应用已从「项目根目录」下的 `.env` **绝对路径**读取（不依赖当前工作目录）。请确认 `.env` 与 `app` 文件夹同级，且变量名为 **`GOLDQUANT_PORT=8085`**（不要写成 `PORT=` 单独一项，除非带前缀约定）。
2. **启动方式**：只有 **`python -m app`**、**`run.sh start`** 会使用 `Settings` 里的端口。若使用命令行 **`uvicorn app.main:app`** 且**未**指定 `--port`，监听端口由 **Uvicorn 默认 8085** 决定，**不会**读取 `.env` 中的 `GOLDQUANT_PORT`。请改用 `python -m app`，或显式：`uvicorn app.main:app --host 0.0.0.0 --port 8085`。
3. 修改 `.env` 后需**重启进程**；若曾启动过，`get_settings()` 有缓存，同一进程内不会自动刷新。

---

## 辅助决策运行方式

推荐使用统一入口：

```powershell
python -m quant <command> [options]
# 或更短：
goldquant <command> [options]
```

如果没有安装可编辑包，也可以使用完整模块名：

```powershell
python -m quant.cli <command> [options]
```

统一入口包含三类命令：

| 命令 | 用途 |
|---|---|
| `run` | 规则引擎链路：取数、生成确定性信号、更新自选状态并推送飞书 |
| `signal` | 从本地或实时数据生成确定性结构化信号，不推送飞书 |
| `replay` | 按 `--mode` 回放 `quant/data/<mode>`，生成确定性结构化信号并可写入文件 |

### 使用本地样例数据

本地样例适合调试规则参数、自选状态更新和规则引擎复现，不依赖实时接口。

```powershell
cd d:\workspace\GoldQuant

# 默认 source=remote；本地数据用 source=local，读取 quant/data/<mode>
python -m quant run --mode post_market_evening --source local

# 盘前 / 盘中 / 午间本地样例
python -m quant run --mode pre_market --source local
python -m quant run --mode during_market --source local
python -m quant run --mode post_market_lunch --source local
```

本地样例路径默认规则为：

```text
quant/data/<mode>
```

机器可读策略配置默认位于：

```text
quant/strategy.yml
```

其中 `mode` 可选：

```text
news
pre_market
during_market
post_market_lunch
post_market_evening
```

也可以只生成结构化信号，不更新状态、不推送飞书：

```powershell
python -m quant replay --mode post_market_evening
python -m quant replay --mode pre_market
python -m quant replay --mode during_market
```

`replay` 会在终端打印结构化信号，并默认写入：

```text
~/.quant/signals/<时间>-<mode>-signals.json
```

如需指定输出文件：

```powershell
python -m quant replay --mode post_market_evening --output result_signals.json
```

### 使用实时数据

实时模式需要先启动 FastAPI 服务，再由统一入口调用本地 API 聚合实时数据。

终端一：启动 API。

```powershell
cd d:\workspace\GoldQuant
python -m app
```

终端二：运行辅助决策。

```powershell
cd d:\workspace\GoldQuant

# 如果 API 使用默认 8085 端口
python -m quant run --mode pre_market --source remote --base-url http://127.0.0.1:8085
python -m quant run --mode during_market --source remote --base-url http://127.0.0.1:8085
python -m quant run --mode post_market_lunch --source remote --base-url http://127.0.0.1:8085
python -m quant run --mode post_market_evening --source remote --base-url http://127.0.0.1:8085

# 如果 .env 配置为 GOLDQUANT_PORT=8085
python -m quant run --mode during_market --source remote --base-url http://127.0.0.1:8085
```

也可以用环境变量固定默认数据源和 API 地址：

```powershell
$env:GOLDQUANT_DATA_SOURCE="remote"
$env:GOLDQUANT_BASE_URL="http://127.0.0.1:8085"

python -m quant run --mode during_market
```

实时接口对应关系：

| 运行模式 | 实时 API |
|---|---|
| `news` | `/api/v1/quant/market/news` |
| `pre_market` | `/api/v1/quant/market/pre_market` |
| `during_market` | `/api/v1/quant/market/during_market` |
| `post_market_lunch` | `/api/v1/quant/market/post_market` |
| `post_market_evening` | `/api/v1/quant/market/post_market` |

量化状态文件统一保存在：

```text
~/.quant
```

主要包括 `optional.jsonl`、`holding.jsonl`、`stoploss.jsonl`、`fund.md`、`signals/` 等。

---

## API 一览

所有业务接口前缀为 **`/api/v1`**。

| 说明 | 方法 | 路径 | 数据来源 |
|------|------|------|----------|
| 雪球热度榜（关注 / 讨论 / 交易） | GET | `/api/v1/hot/xueqiu/{board}` | AKShare：`stock_hot_*_xq` |
| 东方财富飙升榜（A 股） | GET | `/api/v1/hot/eastmoney/surge` | AKShare：`stock_hot_up_em` |
| 东方财富个股人气榜（A 股，约前 100） | GET | `/api/v1/hot/eastmoney/popularity` | AKShare：`stock_hot_rank_em` |
| 东方财富个股资讯 | GET | `/api/v1/news/em?symbol=` | AKShare：`stock_news_em` |
| 个股信息查询（东财） | GET | `/api/v1/stock/em/individual-info?symbol=` | AKShare：`stock_individual_info_em` |
| 同花顺行业列表（概览） | GET | `/api/v1/board/ths/industry-summary` | AKShare：`stock_board_industry_summary_ths` |
| 同花顺热榜（直连 JSON） | GET | `/api/v1/hot/ths` | 同花顺接口（见下） |
| 东财自定义请求头 | POST | `/api/v1/admin/eastmoney/headers` | 写入 `.eastmoney.header`，`requests` 访问东财域名时合并 |

### 路径与查询参数摘要

1. **雪球** `/api/v1/hot/xueqiu/{board}`  
   - `board`：`follow`（关注）｜ `tweet`（讨论）｜ `deal`（交易）  
   - `symbol` 查询参数：`最热门` 或 `本周新增`（默认 `最热门`）  
   - 对应文档：[股票数据 - 股票热度 - 雪球](https://akshare.akfamily.xyz/data/stock/stock.html#股票热度-雪球)

2. **东方财富**  
   - 「飙升榜」与文档中的「人气榜（排名）」在 AKShare 中为不同接口，本服务拆为两个路径以避免混淆：  
     - 飙升榜 → `stock_hot_up_em` → `/hot/eastmoney/surge`  
     - 人气榜（A 股前约 100）→ `stock_hot_rank_em` → `/hot/eastmoney/popularity`  
   - 文档入口：[股票数据 - 股票热度 - 东财](https://akshare.akfamily.xyz/data/stock/stock.html#股票热度-东财)

3. **资讯** `/api/v1/news/em?symbol=603777`  
   - `symbol`：股票代码或搜索关键词（与 AKShare 一致）  
   - 文档：[个股新闻](https://akshare.akfamily.xyz/data/stock/stock.html#个股新闻)

4. **个股信息（东财）** `/api/v1/stock/em/individual-info?symbol=000001`  
   - `symbol`：股票代码（与 AKShare `stock_individual_info_em` 一致）  
   - 文档：[个股信息查询-东财](https://akshare.akfamily.xyz/data/stock/stock.html#个股信息查询-东财)

5. **同花顺行业一览** `/api/v1/board/ths/industry-summary`  
   - 无查询参数，对应 AKShare `stock_board_industry_summary_ths`。

6. **东财自定义请求头** `POST /api/v1/admin/eastmoney/headers`  
   - Body：JSON 数组，`[{"key":"...","value":"..."}, ...]`，每次提交**整份覆盖**项目根目录 `.eastmoney.header`。  
   - 应用启动时对 `requests.Session.request` 打补丁：仅当 URL 包含 `eastmoney.com` 时，从该文件读取并 `update` 到请求头（文件中的键覆盖调用方传入的同名头）。  
   - **安全提示**：该接口可改写对第三方的请求头，生产环境请自行加鉴权或网络隔离。

7. **同花顺热榜** `/api/v1/hot/ths`  
   - 接口基址在代码中写死为 `fuyao/hot_list/.../v1/stock`（不通过环境变量配置）；默认请求与下列 URL 等价（可通过查询参数覆盖路径参数）：  
     `https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal`  
   - 查询参数：`stock_type`、`type`（在 OpenAPI 中为避免与 Python 关键字冲突，代码里使用别名，URL 上仍为 `type`）、`list_type`；可选 `limit` 截取前 N 条 `stock_list`。  
   - 响应中包含上游 JSON（`raw`）；若设置 `limit`，仅截取 `data.stock_list` 前 N 条。另含 `stock_list_total`（截取前总条数）与 `stock_list_returned`（本次返回条数）。

---

## 响应格式约定

除同花顺直连接口外，多数 AKShare 封装接口（东财行情/热度/资讯、新浪、同花顺行业一览等）统一返回 DataFrame 转 JSON 结构：

```json
{
  "source": "akshare.stock_hot_rank_em",
  "params": {},
  "row_count": 100,
  "columns": ["列1", "列2"],
  "rows": [ { "列1": "...", "列2": "..." } ]
}
```

`params` 为本次请求入参回显；`columns` 与每行 `rows` 的键一致。同花顺热榜直连仍返回 `source` / `raw` 等原结构（见上表）。

HTTP **502** 通常表示上游抓取失败或返回异常，响应体中的 `detail` 为错误信息字符串。

---

## 与 OpenClaw 集成思路

将本服务作为本地或内网 HTTP 服务启动后，在 OpenClaw（或任意自动化流程）中通过 `GET` 请求上述路径拉取 JSON，再进入规则引擎。建议：

- 对榜单类接口做请求间隔与缓存，避免触发源站限流；
- 生产环境为服务配置反向代理与鉴权（本仓库默认无认证，仅适合本机或可信网络）。

---

## 本地快速自检

```powershell
# 健康检查
curl http://127.0.0.1:8085/health

# 东财人气榜（需服务已启动且网络正常）
curl http://127.0.0.1:8085/api/v1/hot/eastmoney/popularity
```

亦可在 Python 中使用 `fastapi.testclient.TestClient` 做无端口集成测试。

---

## 许可证与致谢

- 项目代码以你方仓库许可为准。  
- 数据版权归各数据提供方所有；感谢 [AKShare](https://github.com/akfamily/akshare) 项目维护者。
