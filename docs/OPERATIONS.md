# 运维

命令在项目根执行。数据目录 `D:\ProgramData\.quant`（`.env` 的 `GOLDQUANT_QUANT_HOME_DIR`，或命令加 `--home`）。

## 安装

```powershell
cd D:\workspace\GoldQuant
poetry install
copy .env.example .env
```

`.env` 至少写 `GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant`。飞书三项见 [CONFIG.md](./CONFIG.md)。

## 5 板

首次或清库后补近 40 个交易日（只拉涨停池和到过 5 板的票）：

```powershell
poetry run python -m scripts.tactic.backfill --home D:\ProgramData\.quant
```

每个交易日：

```powershell
poetry run python -m scripts.tactic.close --home D:\ProgramData\.quant
poetry run python -m scripts.tactic.intraday --home D:\ProgramData\.quant --loop
```

20:10 复盘自己拉当天涨停、炸板、跌停、昨涨停，并给观察池补日 K。池子或日 K 失败则本次复盘中止，不用空池把票判成断板。盘中从 09:30 到 15:05，每 3 分钟。买只在 14:30 之后；卖出随时。正文不变且没有买卖时不发。

状态：`$QUANT_HOME/store/tactic/state.json`。观察票日 K：`$QUANT_HOME/store/tactic/bars/`。

20:10 另把当天 5 板、观察和持仓的资金流、龙虎榜写到 `$QUANT_HOME/store/tactic/ml/`。没上龙虎榜记 `on_list=false`。策略不读这份数据。拉取失败不影响复盘推送。

严重异动临界涨幅来自东财当日列表。模式 A 的触发日来自东财「已触发」历史，不在本地重算。

## 定时

Windows 任务（`scripts/data/register_update_daily_task.ps1`），以 SYSTEM 运行，开机不必登录。错过计划时间会在开机后补跑。

| 任务 | 时间 | 模式 |
|---|---|---|
| GoldQuantTacticClose | 20:10 | `tactic` |
| GoldQuantTacticIntraday | 09:30 起每 3 分钟到 15:05 | `intraday` |

日志：`$QUANT_HOME/logs/scheduled_post_close.log`。

`poetry run python -m app` 不再注册这些任务，避免和 Windows 任务各推一遍。

## 校验

```powershell
poetry run python -m unittest quant.tactic.test_emotion quant.tactic.test_limits quant.tactic.test_modes quant.tactic.test_yidong quant.data.test_severe_anomaly quant.data.test_session_archive
poetry run python -m scripts.check_layering
```
