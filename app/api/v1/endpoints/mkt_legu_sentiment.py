"""乐咕乐股：市场整体概况类指标。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=["乐咕", "市场情绪", "概况"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/legu/market-activity",
    response_model=AkTableOut,
    summary="赚钱效应分析（乐咕乐股）",
    description="封装 `ak.stock_market_activity_legu`，无入参。",
)
async def legu_market_activity() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_market_activity_legu)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_market_activity_legu", {}, df)
