"""
开发启动入口：在项目根目录执行 `python -m app`（会调用 Uvicorn）。

勿单独运行 `python app/main.py` 或双击 `main.py`——那样只会导入模块并立刻退出，不会出现监听日志。
"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from app.core.config import get_settings

    settings = get_settings()
    print(
        f"[GoldQuant] 监听 http://127.0.0.1:{settings.PORT}/docs "
        f"(host={settings.HOST}, reload={settings.UVICORN_RELOAD}, env={settings.ENV})",
        flush=True,
    )
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.UVICORN_RELOAD,
        log_level="info",
    )


if __name__ == "__main__":
    main()
