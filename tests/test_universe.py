"""Universo: normalização de tickers, parsing da Wikipedia, CSV, inline e cache."""
from __future__ import annotations

import pytest

from src import universe
from src.data.cache import DiskCache
from src.universe import (
    UniverseEntry, UniverseError, get_universe, load_csv_universe, normalize_ticker,
    parse_inline_tickers, parse_wikipedia_html,
)

WIKI_HTML = """
<html><body>
<table class="wikitable"><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td>MMM</td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td></tr>
<tr><td>MMM</td><td>3M duplicado</td><td>Industrials</td><td>Industrial Conglomerates</td></tr>
</table></body></html>
"""


def test_normalize_ticker():
    assert normalize_ticker(" brk.b ") == "BRK-B"
    assert normalize_ticker("BF.B") == "BF-B"
    assert normalize_ticker("AAPL") == "AAPL"


def test_parse_wikipedia_html_dedupes_and_normalizes():
    entries = parse_wikipedia_html(WIKI_HTML, "sp500")
    assert [e.ticker for e in entries] == ["MMM", "BRK-B"]
    assert entries[1].name == "Berkshire Hathaway"
    assert entries[1].gics_sector == "Financials"
    assert entries[0].source == "sp500"


def test_parse_wikipedia_html_without_symbol_table_raises():
    with pytest.raises(UniverseError):
        parse_wikipedia_html("<table><tr><th>Foo</th></tr><tr><td>1</td></tr></table>", "sp500")


def test_parse_inline_tickers():
    entries = parse_inline_tickers("aapl, MSFT;asml,aapl,,")
    assert [e.ticker for e in entries] == ["AAPL", "MSFT", "ASML"]
    with pytest.raises(UniverseError):
        parse_inline_tickers(" , ")


def test_load_csv_universe(tmp_path):
    path = tmp_path / "lista.csv"
    path.write_text("Ticker,Name\nnvda,Nvidia\nbrk.b,Berkshire\nnvda,dup\n", encoding="utf-8")
    entries = load_csv_universe(path, ticker_column="ticker")
    assert [e.ticker for e in entries] == ["NVDA", "BRK-B"]
    assert entries[0].name == "Nvidia"
    with pytest.raises(UniverseError):
        load_csv_universe(tmp_path / "nao_existe.csv")


def test_get_universe_csv_and_unknown(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("symbol\nAAPL\n", encoding="utf-8")
    assert [e.ticker for e in get_universe(str(path))] == ["AAPL"]
    with pytest.raises(UniverseError):
        get_universe("nasdaq100")


def test_get_universe_uses_cache_after_first_fetch(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(index_name, timeout=30):
        calls["n"] += 1
        return [UniverseEntry(ticker="AAPL", name="Apple", source=index_name)]

    monkeypatch.setattr(universe, "fetch_wikipedia_index", fake_fetch)
    with DiskCache(tmp_path / "c.sqlite") as cache:
        first = get_universe("sp500", cache, ttl_hours=168)
        second = get_universe("sp500", cache, ttl_hours=168)
        assert first == second
        assert calls["n"] == 1
        get_universe("sp500", cache, ttl_hours=168, refresh=True)
        assert calls["n"] == 2


def test_get_universe_falls_back_to_stale_cache_when_offline(tmp_path, monkeypatch):
    def offline(index_name, timeout=30):
        raise UniverseError("sem rede")

    monkeypatch.setattr(universe, "fetch_wikipedia_index", offline)
    with DiskCache(tmp_path / "c.sqlite") as cache:
        cache.set("universe:sp500", {"entries": [{"ticker": "MSFT", "name": "", "gics_sector": "", "gics_sub_industry": "", "source": "sp500"}]})
        assert [e.ticker for e in get_universe("sp500", cache, ttl_hours=0)] == ["MSFT"]
        with pytest.raises(UniverseError):
            get_universe("sp400", cache)
