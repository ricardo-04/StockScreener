"""Serialização JSON de TickerData (ida e volta pela cache) e lógica de retry."""
from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from src.data import fetcher
from src.data.fetcher import FetchError, FetchSettings, PermanentFetchError, TickerData, fetch_ticker
from tests.conftest import FISCAL_YEARS, make_ticker_data


def test_payload_roundtrip_preserves_statements_prices_and_estimates():
    estimates = {"revenue_estimate": pd.DataFrame({"growth": [0.1, 0.2], "currency": ["USD", "USD"]}, index=["0y", "+1y"])}
    data = make_ticker_data(estimates=estimates)
    data.income.loc["Interest Expense", FISCAL_YEARS[0]] = float("nan")

    payload = json.loads(json.dumps(data.to_payload(), ensure_ascii=False))  # como na cache
    restored = TickerData.from_payload(payload)

    assert restored.ticker == "TEST"
    assert restored.from_cache is True
    assert list(restored.income.columns) == FISCAL_YEARS
    assert restored.income.loc["Total Revenue", FISCAL_YEARS[0]] == 1000.0
    assert math.isnan(restored.income.loc["Interest Expense", FISCAL_YEARS[0]])
    assert restored.balance.loc["Total Debt", FISCAL_YEARS[3]] == 400.0
    assert restored.cashflow.loc["Capital Expenditure", FISCAL_YEARS[0]] == -60.0
    assert isinstance(restored.prices.index, pd.DatetimeIndex)
    assert len(restored.prices) == 600
    assert restored.prices["Close"].iloc[0] == pytest.approx(100.0)
    assert restored.estimates["revenue_estimate"].loc["+1y", "growth"] == 0.2
    assert restored.estimates["revenue_estimate"].loc["0y", "currency"] == "USD"
    assert "companyOfficers" not in restored.info  # listas aninhadas são descartadas
    assert restored.info["marketCap"] == 5_000_000_000


def test_empty_frames_survive_roundtrip():
    data = make_ticker_data()
    data.income = pd.DataFrame()
    data.prices = pd.DataFrame()
    restored = TickerData.from_payload(json.loads(json.dumps(data.to_payload())))
    assert restored.income.empty
    assert restored.prices.empty
    assert not restored.balance.empty


def test_fetch_ticker_retries_transient_errors_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(ticker, settings):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset")
        return make_ticker_data(ticker=ticker)

    monkeypatch.setattr(fetcher, "_download", flaky)
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_: None)
    data = fetch_ticker("TEST", FetchSettings(retries=3, backoff_base_seconds=0.0))
    assert data.ticker == "TEST"
    assert calls["n"] == 3


def test_fetch_ticker_gives_up_after_retries(monkeypatch):
    def always_fails(ticker, settings):
        raise TimeoutError("timed out")

    monkeypatch.setattr(fetcher, "_download", always_fails)
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_: None)
    with pytest.raises(FetchError) as excinfo:
        fetch_ticker("TEST", FetchSettings(retries=2))
    assert "3 tentativas" in str(excinfo.value)


def test_fetch_ticker_does_not_retry_permanent_errors(monkeypatch):
    calls = {"n": 0}

    def invalid(ticker, settings):
        calls["n"] += 1
        raise PermanentFetchError("info vazio")

    monkeypatch.setattr(fetcher, "_download", invalid)
    with pytest.raises(PermanentFetchError):
        fetch_ticker("XXXX", FetchSettings(retries=3))
    assert calls["n"] == 1


def test_fetch_many_uses_cache_and_records_failures(tmp_path, monkeypatch):
    from src.data.cache import DiskCache

    cache = DiskCache(tmp_path / "cache.sqlite", ttl_hours=24)
    downloads: list[str] = []

    def fake_download(ticker, settings):
        downloads.append(ticker)
        if ticker == "BAD":
            raise PermanentFetchError("ticker inválido")
        return make_ticker_data(ticker=ticker)

    monkeypatch.setattr(fetcher, "_download", fake_download)
    settings = FetchSettings(max_workers=2, request_delay_seconds=0.0)

    datas, failures = fetcher.fetch_many(["AAA", "BAD", "BBB"], cache, settings)
    assert [d.ticker for d in datas] == ["AAA", "BBB"]
    assert [f.ticker for f in failures] == ["BAD"]
    assert "ticker inválido" in failures[0].reason
    assert sorted(downloads) == ["AAA", "BAD", "BBB"]

    # Segunda execução: AAA e BBB vêm da cache, BAD volta a ser tentado
    downloads.clear()
    datas, failures = fetcher.fetch_many(["AAA", "BAD", "BBB"], cache, settings)
    assert all(d.from_cache for d in datas)
    assert downloads == ["BAD"]

    # --refresh-cache ignora a cache
    downloads.clear()
    datas, _ = fetcher.fetch_many(["AAA"], cache, settings, refresh=True)
    assert downloads == ["AAA"] and not datas[0].from_cache
    cache.close()
