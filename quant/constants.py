"""策略常量（全项目仅主升浪战法）。"""

STRATEGY_NAME = "主升浪战法"

# 买入信号子类型（三确认链按 code+action+kind 聚合）
BUY_KIND_ASCENT = "上升途中"
BUY_KIND_PULLBACK = "回调企稳"
SELL_KIND_MA5_BREAK = "破5日线"
SELL_KIND_TREND_ERODE = "趋势衰竭"
SELL_KIND_INTRADAY_WEAK = "日内走弱"
SELL_KIND_TIME_STOP = "时间止损"
SELL_KIND_SCORE_WEAK = "评分走弱"
# 叙事「暂不减」缓冲：卖出用裸 sell_threshold，文案用 threshold+HOLD_SCORE_BUFFER
HOLD_SCORE_BUFFER = 8
