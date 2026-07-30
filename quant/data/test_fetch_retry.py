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
