# GoldQuant

A 股 5 板短线战术：盘后飞书复盘，盘中按计划推送。不接券商。另外提供 FastAPI 行情代理。

细节见 [docs/README.md](docs/README.md)。

```powershell
poetry install
# .env：GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant
poetry run python -m scripts.tactic.backfill --home D:\ProgramData\.quant
poetry run python -m scripts.tactic.close --home D:\ProgramData\.quant
poetry run python -m scripts.tactic.intraday --home D:\ProgramData\.quant --loop
```

Windows 任务：20:10 复盘、09:30 盘中每 3 分钟。注册脚本 `scripts/data/register_update_daily_task.ps1`（SYSTEM，开机不必登录）。
