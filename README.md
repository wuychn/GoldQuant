# GoldQuant Data API

面向 **OpenClaw** 等自动化决策工具的股票**热度榜**与**资讯**数据服务。服务端使用 [FastAPI](https://fastapi.tiangolo.com/)，数据层主要调用 [AKShare](https://github.com/akfamily/akshare) 及同花顺公开 JSON 接口。

> **说明**：本服务仅做数据聚合与转发，不构成投资建议。行情与榜单数据来自第三方网站，存在延迟、字段变更或访问失败的可能。

---

## 项目结构（FastAPI 常见布局）

```text
GoldQuant/
├── app/
│   ├── main.py                 # 应用入口：`create_app()` 与全局 `app` 实例
│   ├── __main__.py             # `python -m app` 时调用 Uvicorn 真正启动服务
│   ├── api/
│   │   ├── deps.py             # 依赖注入（如 `SettingsDep`）
│   │   └── v1/
│   │       ├── router.py       # 聚合 v1 子路由
│   │       └── endpoints/
│   │           └── data.py     # 热度榜、资讯、同花顺等 HTTP 端点
│   ├── core/
│   │   ├── config.py         # `pydantic-settings`：环境变量与 `.env`
│   │   └── proxy.py          # 出站代理：写入进程 HTTP(S)_PROXY
│   └── utils/
│       └── dataframe.py      # DataFrame → JSON 行列表
├── .env.example                # 配置项说明模板（复制为 `.env` 使用）
├── run.ps1                     # Windows：一键调用 `python -m app`（需在项目根目录）
├── run.sh                      # Linux：./run.sh start|stop|restart（PID: goldquant.pid）
├── pyproject.toml              # 包元数据与依赖声明（可 `pip install -e .`）
├── requirements.txt            # 与 pyproject 依赖对齐，便于传统 `pip install -r`
└── README.md
```

---

## 配置说明

- 配置类位于 `app/core/config.py`，通过 **`pydantic-settings`** 读取环境变量，**前缀为 `GOLDQUANT_`**（例如 `GOLDQUANT_ENV`）。
- 项目根目录下的 **`.env`** 使用**固定绝对路径**加载（相对 `app/core/config.py` 解析），与从哪个目录启动无关；勿提交仓库（已列入 `.gitignore`），字段说明见 **`.env.example`**。
- 常用项：`GOLDQUANT_CORS_ORIGINS`（跨域源，`*` 或逗号分隔）、`GOLDQUANT_HTTP_CLIENT_TIMEOUT`、`GOLDQUANT_THS_HOT_URL`、`GOLDQUANT_THS_DEFAULT_USER_AGENT` 等。
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

## 启动服务

**必须在项目根目录 `GoldQuant` 下执行**（保证能 `import app`），并已激活 venv。

推荐任选其一：

```powershell
# 方式 A：模块入口（会读取 .env 中的 HOST/PORT/RELOAD，并打印监听地址）
python -m app

# 方式 B：与官方文档一致的 Uvicorn 命令行
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 方式 C：Windows 下可双击或在项目根执行根目录的 run.ps1
.\run.ps1
```

**Linux 后台启动 / 停止 / 重启**（项目根目录 `run.sh`，使用 `nohup`，**无热重载**，适合服务器常驻）：

```bash
chmod +x run.sh
./run.sh start    # 写入 goldquant.pid，日志追加到 logs/goldquant.log
./run.sh stop     # 按 PID 优雅停止，必要时 SIGKILL
./run.sh restart  # 先 stop（若曾用本脚本启动），再 start
```

首次使用前需已创建 `.venv` 并安装依赖。若存在 `.env`，启动前会 `source` 以读取 `GOLDQUANT_HOST`、`GOLDQUANT_PORT` 等变量（默认 `0.0.0.0:8000`）。

- 交互式 API 文档：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- 健康检查：<http://127.0.0.1:8000/health>

### 启动后立刻退出、且几乎没有输出？

常见原因是 **只运行了 `main.py` 文件**（例如在资源管理器中双击 `main.py`，或在 IDE 里用「运行当前文件」打开 `app/main.py`）。该文件只**定义** FastAPI 应用对象，**不会**调用 Uvicorn，进程会正常结束（退出码 0），看起来像「闪退」。

请改用上面的 **`python -m app`** 或 **`uvicorn app.main:app ...`**，并确认**工作目录**为项目根目录。若仍异常，在 PowerShell 中执行 `cd d:\workspace\GoldQuant`（换成你的路径）后再启动。

监听地址与端口可通过环境变量 **`GOLDQUANT_HOST` / `GOLDQUANT_PORT`**（见 `.env.example`）调整。

### `.env` 里配置了 `GOLDQUANT_PORT` 仍不生效？

1. **配置加载位置**：应用已从「项目根目录」下的 `.env` **绝对路径**读取（不依赖当前工作目录）。请确认 `.env` 与 `app` 文件夹同级，且变量名为 **`GOLDQUANT_PORT=8085`**（不要写成 `PORT=` 单独一项，除非带前缀约定）。
2. **启动方式**：只有 **`python -m app`**、**`run.ps1`**、**`run.sh start`** 会使用 `Settings` 里的端口。若使用命令行 **`uvicorn app.main:app`** 且**未**指定 `--port`，监听端口由 **Uvicorn 默认 8000** 决定，**不会**读取 `.env` 中的 `GOLDQUANT_PORT`。请改用 `python -m app`，或显式：`uvicorn app.main:app --host 0.0.0.0 --port 8085`。
3. 修改 `.env` 后需**重启进程**；若曾启动过，`get_settings()` 有缓存，同一进程内不会自动刷新。

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
   - 默认请求与下列 URL 等价（可通过查询参数覆盖）：  
     `https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal`  
   - 查询参数：`stock_type`、`type`（在 OpenAPI 中为避免与 Python 关键字冲突，代码里使用别名，URL 上仍为 `type`）、`list_type`；可选 `limit` 截取前 N 条 `stock_list`。  
   - 响应中包含上游 JSON（`raw`）；若设置 `limit`，仅截取 `data.stock_list` 前 N 条。另含 `stock_list_total`（截取前总条数）与 `stock_list_returned`（本次返回条数）。

---

## 响应格式约定

除同花顺直连接口外，AKShare 封装接口统一返回类似结构：

```json
{
  "source": "akshare.stock_hot_rank_em",
  "row_count": 100,
  "rows": [ { "...": "..." } ]
}
```

雪球接口额外包含 `board`、`symbol` 字段；资讯接口包含查询所用的 `symbol`。

HTTP **502** 通常表示上游抓取失败或返回异常，响应体中的 `detail` 为错误信息字符串。

---

## 与 OpenClaw 集成思路

将本服务作为本地或内网 HTTP 服务启动后，在 OpenClaw（或任意自动化流程）中通过 `GET` 请求上述路径拉取 JSON，再进入你的规则引擎或 LLM 工具链。建议：

- 对榜单类接口做请求间隔与缓存，避免触发源站限流；
- 生产环境为服务配置反向代理与鉴权（本仓库默认无认证，仅适合本机或可信网络）。

---

## 本地快速自检

```powershell
# 健康检查
curl http://127.0.0.1:8000/health

# 东财人气榜（需服务已启动且网络正常）
curl http://127.0.0.1:8000/api/v1/hot/eastmoney/popularity
```

亦可在 Python 中使用 `fastapi.testclient.TestClient` 做无端口集成测试。

---

## 许可证与致谢

- 项目代码以你方仓库许可为准。  
- 数据版权归各数据提供方所有；感谢 [AKShare](https://github.com/akfamily/akshare) 项目维护者。
