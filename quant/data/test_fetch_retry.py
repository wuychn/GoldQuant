"""fetch 层 ``_retry`` 退避重试单测。"""

from __future__ import annotations

import pandas as pd
import pytest

from quant.data.sources.daily._shared import _retry


def test_retry_success_first_try(monkeypatch):
    """正常返回非空 df，不重试、不 sleep。"""
    sleeps: list[float] = []
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return pd.DataFrame({"a": [1, 2]})

    df = _retry(fn, retries=4, base=1.0, label="t")
    assert not df.empty
    assert calls["n"] == 1
    assert sleeps == []


def test_retry_empty_then_ok(monkeypatch):
    """前几次返回空，最后非空 → 返回非空，中间有 sleep。"""
    sleeps: list[float] = []
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: sleeps.append(s))
    seq = iter([pd.DataFrame(), pd.DataFrame(), pd.DataFrame({"a": [1]})])

    df = _retry(lambda: next(seq), retries=4, base=1.0, label="t")
    assert not df.empty
    assert sleeps == [1.0, 2.0]  # i=0, i=1 各一次


def test_retry_exhausted_raises(monkeypatch):
    """始终空 → 重试耗尽抛异常。"""
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: None)
    with pytest.raises(Exception):
        _retry(lambda: pd.DataFrame(), retries=2, base=0, label="t")


def test_retry_empty_ok_returns_empty(monkeypatch):
    """empty_ok=True 时空数据耗尽返回空帧，不抛。"""
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: None)
    df = _retry(lambda: pd.DataFrame(), retries=2, base=0, label="hist x", empty_ok=True)
    assert df is not None and df.empty


def test_retry_exception_retried(monkeypatch):
    """异常也重试，最终抛出。"""
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _retry(fn, retries=2, base=0, label="t")
    assert calls["n"] == 3  # 初次 + 2 次重试


def test_retry_rate_limit_doubles_sleep(monkeypatch):
    """限流异常（含 429）退避加倍。"""
    sleeps: list[float] = []
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: sleeps.append(s))

    def fn():
        raise RuntimeError("429 Too Many Requests")

    with pytest.raises(RuntimeError):
        _retry(fn, retries=2, base=1.0, label="t")
    # 普通应为 1, 2；限流加倍 → 2, 4
    assert sleeps == [2.0, 4.0]


def test_retry_normal_exception_no_double(monkeypatch):
    """非限流异常正常退避（不加倍）。"""
    sleeps: list[float] = []
    monkeypatch.setattr("quant.data.sources.daily._shared.time.sleep", lambda s: sleeps.append(s))

    def fn():
        raise ConnectionError("network down")

    with pytest.raises(ConnectionError):
        _retry(fn, retries=3, base=1.0, label="t")
    assert sleeps == [1.0, 2.0, 4.0]


def test_eastmoney_index_kline_code_not_nan(monkeypatch):
    """eastmoney_index_kline 返回的 ``code`` 列必须非 NaN（修前：空帧上
    ``out["code"]=scalar`` 再后续 Series 展开 → code 退化为 NaN，导致
    ``read_index_daily`` 按 code 过滤返回空 → 回测基准/超额全 0）。"""
    import requests

    import common.utils.source_headers as sh
    from quant.data.sources.daily._shared import eastmoney_index_kline

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": {
                    "klines": [
                        "2024-01-02,10,11,12,9,1000,10000,0.1",
                        "2024-01-03,11,12,13,10,1100,11000,0.1",
                    ]
                }
            }

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(sh, "load_headers_from_file", lambda: {})
    df = eastmoney_index_kline("000300", start="2024-01-01", end="2024-01-31")
    assert not df.empty
    assert df["code"].notna().all(), df["code"].tolist()
    assert (df["code"] == "000300").all()
    assert list(df.columns) == [
        "code", "date", "open", "high", "low", "close", "volume", "amount"
    ] or {"code", "date", "close"}.issubset(df.columns)
