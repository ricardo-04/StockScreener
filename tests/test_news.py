"""Notícias: parsing dos formatos do yfinance (novo e antigo) e da Finnhub, deduplicação, ordenação e cache."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src import news as news_module
from src.data.cache import DiskCache
from src.news import (
    NewsItem, dedupe_and_sort, get_company_news, normalize_title, parse_finnhub_item, parse_yfinance_item,
    to_datetime,
)

NEW_FORMAT = {
    "id": "abc",
    "content": {
        "title": "Apple unveils new chips",
        "summary": "Resumo da notícia.",
        "pubDate": "2026-10-13T14:30:00Z",
        "provider": {"displayName": "Reuters"},
        "canonicalUrl": {"url": "https://example.com/apple-chips"},
        "clickThroughUrl": {"url": "https://finance.yahoo.com/news/apple-chips"},
        "contentType": "STORY",
    },
}
OLD_FORMAT = {
    "uuid": "1",
    "title": "Apple results beat estimates",
    "publisher": "Bloomberg",
    "link": "https://example.com/apple-results",
    "providerPublishTime": "1791992083",
    "type": "STORY",
    "relatedTickers": ["AAPL", "MSFT"],
}


def test_to_datetime_accepts_epoch_iso_and_datetime():
    assert to_datetime("1791992083") == datetime.fromtimestamp(1791992083, tz=timezone.utc)
    assert to_datetime(1791992083000) == datetime.fromtimestamp(1791992083, tz=timezone.utc)  # milissegundos
    assert to_datetime("2026-10-13T14:30:00Z") == datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)
    assert to_datetime(datetime(2026, 1, 1)).tzinfo is not None
    assert to_datetime(None) is None and to_datetime("lixo") is None


def test_parse_new_yfinance_format():
    item = parse_yfinance_item(NEW_FORMAT, "AAPL")
    assert item.title == "Apple unveils new chips"
    assert item.source == "Reuters"
    assert item.link == "https://example.com/apple-chips"
    assert item.summary == "Resumo da notícia."
    assert item.published == datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)
    assert item.origin == "yfinance"
    assert item.published_text == "2026-10-13"


def test_parse_old_yfinance_format_and_related_tickers_filter():
    item = parse_yfinance_item(OLD_FORMAT, "AAPL", origin="yfinance_search")
    assert item.source == "Bloomberg" and item.origin == "yfinance_search"
    assert item.published == datetime.fromtimestamp(1791992083, tz=timezone.utc)
    assert parse_yfinance_item(OLD_FORMAT, "NVDA") is None  # notícia de outra empresa
    assert parse_yfinance_item({"content": {"summary": "sem título"}}, "AAPL") is None
    assert parse_yfinance_item("não é dict", "AAPL") is None


def test_parse_finnhub_item():
    item = parse_finnhub_item({"headline": "Apple sued", "source": "WSJ", "datetime": 1791992083,
                               "url": "https://example.com/sued", "summary": "Processo."})
    assert item.origin == "finnhub" and item.title == "Apple sued" and item.summary == "Processo."
    assert parse_finnhub_item({"source": "x"}) is None


def test_dedupe_sort_and_cap():
    base = datetime(2026, 10, 10, tzinfo=timezone.utc)
    items = [
        NewsItem("Apple results beat estimates", "A", base, "https://x/1", ""),
        NewsItem("APPLE results -- beat estimates!", "B", base.replace(day=11), "https://x/2", ""),  # mesmo título normalizado
        NewsItem("Outra", "C", base.replace(day=12), "https://x/1/", ""),                           # mesmo link
        NewsItem("Sem data", "D", None, "https://x/3", ""),
        NewsItem("Mais recente", "E", base.replace(day=13), "https://x/4", ""),
    ]
    result = dedupe_and_sort(items, max_items=15)
    assert [it.title for it in result] == ["Mais recente", "Apple results beat estimates", "Sem data"]
    assert len(dedupe_and_sort(items, max_items=1)) == 1
    assert normalize_title("APPLE results -- beat estimates!") == "apple results beat estimates"


def test_get_company_news_combines_sources_and_uses_cache(tmp_path, monkeypatch):
    calls = {"yf": 0, "fh": 0}

    def fake_yf(ticker, count=20, search_fallback=True):
        calls["yf"] += 1
        return [NewsItem("Y1", "Yahoo", datetime(2026, 10, 1, tzinfo=timezone.utc), "https://y/1", "", "yfinance")]

    def fake_fh(ticker, api_key, days=30, timeout=20):
        calls["fh"] += 1
        return [NewsItem("F1", "Finnhub", datetime(2026, 10, 5, tzinfo=timezone.utc), "https://f/1", "", "finnhub")]

    monkeypatch.setattr(news_module, "fetch_yfinance_news", fake_yf)
    monkeypatch.setattr(news_module, "fetch_finnhub_news", fake_fh)
    cfg = {"news": {"max_items": 15, "cache_ttl_hours": 6}}

    with DiskCache(tmp_path / "c.sqlite") as cache:
        items = get_company_news("AAPL", cfg, cache, finnhub_key=None)
        assert [it.title for it in items] == ["Y1"] and calls == {"yf": 1, "fh": 0}
        items = get_company_news("AAPL", cfg, cache, finnhub_key=None)  # cache
        assert [it.title for it in items] == ["Y1"] and calls["yf"] == 1
        items = get_company_news("AAPL", cfg, cache, finnhub_key="k", refresh=True)
        assert [it.title for it in items] == ["F1", "Y1"] and calls == {"yf": 2, "fh": 1}
        assert items[0].published_text == "2026-10-05"


def test_fetch_news_for_tickers(monkeypatch):
    monkeypatch.setattr(news_module, "get_company_news", lambda t, cfg, cache, key, refresh: [NewsItem(t, "s", None, "", "")])
    result = news_module.fetch_news_for_tickers(["A", "B"], {"news": {"request_delay_seconds": 0}}, None, None, False)
    assert set(result) == {"A", "B"} and result["A"][0].title == "A"


def test_news_item_roundtrip():
    item = NewsItem("T", "S", datetime(2026, 10, 1, tzinfo=timezone.utc), "L", "R", "finnhub")
    restored = NewsItem.from_dict(item.to_dict())
    assert restored == item
    assert NewsItem.from_dict({"title": "x"}).published is None
