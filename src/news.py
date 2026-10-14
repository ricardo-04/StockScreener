"""Notícias por empresa (só para o Top N).

Fontes:
- gratuita, por omissão: ``yfinance.Ticker.get_news`` (formato novo, com os campos
  dentro de ``content``) e, se vier vazio, ``yfinance.Search(ticker).news``
  (formato antigo: ``title``/``publisher``/``link``/``providerPublishTime``);
- opcional, se existir ``FINNHUB_API_KEY``: endpoint ``company-news`` dos últimos N dias.

Cada notícia guarda título, fonte, data, link e resumo. Os duplicados são
removidos (mesmo link ou mesmo título normalizado), as notícias são ordenadas
por data (mais recentes primeiro) e limitadas a ``news.max_items``.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.data.cache import DiskCache

log = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
ORIGIN_YFINANCE = "yfinance"
ORIGIN_YFINANCE_SEARCH = "yfinance_search"
ORIGIN_FINNHUB = "finnhub"


@dataclass
class NewsItem:
    title: str
    source: str
    published: datetime | None
    link: str
    summary: str
    origin: str = ORIGIN_YFINANCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "published": self.published.isoformat() if self.published else None,
            "link": self.link,
            "summary": self.summary,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        return cls(
            title=str(data.get("title") or ""),
            source=str(data.get("source") or ""),
            published=to_datetime(data.get("published")),
            link=str(data.get("link") or ""),
            summary=str(data.get("summary") or ""),
            origin=str(data.get("origin") or ORIGIN_YFINANCE),
        )

    @property
    def published_text(self) -> str:
        return self.published.strftime("%Y-%m-%d") if self.published else "s/ data"


# ---------------------------------------------------------------------------
# Conversão de datas e normalização
# ---------------------------------------------------------------------------
def to_datetime(value: Any) -> datetime | None:
    """Aceita epoch (int/float/str numérica), ISO 8601 ou datetime; devolve UTC ou None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            seconds = float(value)
            if seconds > 1e12:  # milissegundos
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


# ---------------------------------------------------------------------------
# Parsing das fontes
# ---------------------------------------------------------------------------
def parse_yfinance_item(item: dict[str, Any], ticker: str, origin: str = ORIGIN_YFINANCE) -> NewsItem | None:
    """Aceita o formato novo (campos em ``item['content']``) e o antigo (campos no topo)."""
    if not isinstance(item, dict):
        return None
    content = item.get("content") if isinstance(item.get("content"), dict) else None
    if content:
        title = content.get("title")
        summary = content.get("summary") or content.get("description") or ""
        published = to_datetime(content.get("pubDate") or content.get("displayTime"))
        provider = content.get("provider")
        source = provider.get("displayName") if isinstance(provider, dict) else (provider or "")
        canonical = content.get("canonicalUrl") or {}
        click = content.get("clickThroughUrl") or {}
        link = (canonical.get("url") if isinstance(canonical, dict) else "") or (click.get("url") if isinstance(click, dict) else "") or ""
    else:
        title = item.get("title")
        summary = item.get("summary") or ""
        published = to_datetime(item.get("providerPublishTime"))
        source = item.get("publisher") or ""
        link = item.get("link") or ""
        related = item.get("relatedTickers")
        if isinstance(related, (list, tuple)) and related:
            if ticker.upper() not in {str(r).upper() for r in related}:
                return None  # notícia de outra empresa devolvida pela pesquisa
    if not title or not str(title).strip():
        return None
    return NewsItem(
        title=str(title).strip(),
        source=str(source or "").strip(),
        published=published,
        link=str(link or "").strip(),
        summary=str(summary or "").strip(),
        origin=origin,
    )


def parse_finnhub_item(item: dict[str, Any]) -> NewsItem | None:
    if not isinstance(item, dict) or not item.get("headline"):
        return None
    return NewsItem(
        title=str(item.get("headline")).strip(),
        source=str(item.get("source") or "").strip(),
        published=to_datetime(item.get("datetime")),
        link=str(item.get("url") or "").strip(),
        summary=str(item.get("summary") or "").strip(),
        origin=ORIGIN_FINNHUB,
    )


# ---------------------------------------------------------------------------
# Fontes
# ---------------------------------------------------------------------------
def fetch_yfinance_news(ticker: str, count: int = 20, search_fallback: bool = True) -> list[NewsItem]:
    """``Ticker.get_news`` e, se vier vazio, ``Search(ticker).news``. Nunca lança: devolve [] com aviso."""
    import yfinance as yf  # import tardio: só o Top N precisa de notícias

    items: list[NewsItem] = []
    try:
        raw = yf.Ticker(ticker).get_news(count=count) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: Ticker.news falhou (%s: %s)", ticker, type(exc).__name__, str(exc)[:120])
        raw = []
    items = [parsed for parsed in (parse_yfinance_item(it, ticker) for it in raw) if parsed]

    if not items and search_fallback:
        try:
            raw = yf.Search(ticker, news_count=count).news or []
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: Search.news falhou (%s: %s)", ticker, type(exc).__name__, str(exc)[:120])
            raw = []
        items = [parsed for parsed in (parse_yfinance_item(it, ticker, ORIGIN_YFINANCE_SEARCH) for it in raw) if parsed]
    return items


def fetch_finnhub_news(ticker: str, api_key: str, days: int = 30, timeout: int = 20) -> list[NewsItem]:
    """Endpoint company-news da Finnhub (últimos ``days`` dias). Nunca lança: devolve [] com aviso."""
    today = datetime.now(timezone.utc).date()
    params = {
        "symbol": ticker,
        "from": (today - timedelta(days=days)).isoformat(),
        "to": today.isoformat(),
        "token": api_key,
    }
    try:
        response = requests.get(FINNHUB_URL, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("%s: Finnhub falhou (%s: %s)", ticker, type(exc).__name__, str(exc)[:120])
        return []
    if not isinstance(payload, list):
        return []
    return [parsed for parsed in (parse_finnhub_item(it) for it in payload) if parsed]


# ---------------------------------------------------------------------------
# Agregação
# ---------------------------------------------------------------------------
def dedupe_and_sort(items: list[NewsItem], max_items: int = 15) -> list[NewsItem]:
    """Remove duplicados (link ou título normalizado), ordena por data desc e limita."""
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        link_key = item.link.strip().lower().rstrip("/") if item.link else ""
        title_key = normalize_title(item.title)
        if (link_key and link_key in seen_links) or (title_key and title_key in seen_titles):
            continue
        if link_key:
            seen_links.add(link_key)
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)
    unique.sort(key=lambda it: (it.published is None, -(it.published.timestamp() if it.published else 0.0)))
    return unique[: max(0, int(max_items))]


def get_company_news(
    ticker: str,
    cfg: dict[str, Any],
    cache: DiskCache | None = None,
    finnhub_key: str | None = None,
    refresh: bool = False,
) -> list[NewsItem]:
    """Notícias de uma empresa, combinando as fontes disponíveis, com cache de curta duração."""
    news_cfg = cfg.get("news", {}) or {}
    max_items = int(news_cfg.get("max_items", 15))
    cache_key = f"news:{ticker}"
    ttl = float(news_cfg.get("cache_ttl_hours", 6))

    if cache is not None and not refresh:
        payload = cache.get(cache_key, ttl_hours=ttl)
        if payload is not None and isinstance(payload.get("items"), list):
            return [NewsItem.from_dict(it) for it in payload["items"]][:max_items]

    items = fetch_yfinance_news(
        ticker,
        count=int(news_cfg.get("yfinance_count", 20)),
        search_fallback=bool(news_cfg.get("search_fallback", True)),
    )
    if finnhub_key and news_cfg.get("use_finnhub_if_available", True):
        items += fetch_finnhub_news(ticker, finnhub_key, days=int(news_cfg.get("finnhub_days", 30)))

    result = dedupe_and_sort(items, max_items)
    if cache is not None:
        cache.set(cache_key, {"items": [it.to_dict() for it in result]})
    return result


def fetch_news_for_tickers(
    tickers: list[str],
    cfg: dict[str, Any],
    cache: DiskCache | None = None,
    finnhub_key: str | None = None,
    refresh: bool = False,
) -> dict[str, list[NewsItem]]:
    """Notícias para vários tickers, em sequência (volume pequeno: só o Top N)."""
    delay = float((cfg.get("news", {}) or {}).get("request_delay_seconds", 0.3))
    result: dict[str, list[NewsItem]] = {}
    for ticker in tickers:
        result[ticker] = get_company_news(ticker, cfg, cache, finnhub_key, refresh)
        if delay > 0:
            time.sleep(delay)
    total = sum(len(v) for v in result.values())
    log.info("Notícias: %d itens para %d empresas (Finnhub %s)", total, len(tickers), "ativa" if finnhub_key else "inativa")
    return result
