"""Universo de tickers (Etapa 0 do funil).

Fontes suportadas:
- índices S&P 500 / 400 / 600 (tabelas da Wikipedia) e a união ``sp1500``;
- lista custom em CSV (coluna configurável, por omissão ``ticker``);
- lista inline passada na linha de comandos (``--tickers AAPL,MSFT``).

As listas dos índices ficam em cache com TTL próprio (7 dias por omissão), para
que o pipeline funcione offline depois da primeira execução.
"""
from __future__ import annotations

import io
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import requests

from src.data.cache import DiskCache

log = logging.getLogger(__name__)

WIKIPEDIA_SOURCES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}
COMPOSITE_INDICES = {"sp1500": ("sp500", "sp400", "sp600")}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SYMBOL_COLUMNS = ("symbol", "ticker", "ticker symbol")
NAME_COLUMNS = ("security", "company", "company name", "name")
SECTOR_COLUMNS = ("gics sector", "sector")
SUB_INDUSTRY_COLUMNS = ("gics sub-industry", "sub-industry", "industry")


class UniverseError(RuntimeError):
    """Não foi possível obter o universo pedido."""


@dataclass(frozen=True)
class UniverseEntry:
    ticker: str
    name: str = ""
    gics_sector: str = ""
    gics_sub_industry: str = ""
    source: str = ""


def normalize_ticker(symbol: object) -> str:
    """Normaliza um símbolo para o formato do Yahoo Finance (``BRK.B`` -> ``BRK-B``)."""
    return str(symbol).strip().upper().replace(".", "-")


def _find_column(columns, candidates) -> str | None:
    lowered = {str(col).strip().lower(): col for col in columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _clean(value) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def _entries_from_table(table: pd.DataFrame, source: str, symbol_column: str) -> list[UniverseEntry]:
    name_col = _find_column(table.columns, NAME_COLUMNS)
    sector_col = _find_column(table.columns, SECTOR_COLUMNS)
    sub_col = _find_column(table.columns, SUB_INDUSTRY_COLUMNS)

    entries: list[UniverseEntry] = []
    seen: set[str] = set()
    for _, row in table.iterrows():
        ticker = normalize_ticker(row[symbol_column])
        if not ticker or ticker == "NAN" or ticker in seen:
            continue
        seen.add(ticker)
        entries.append(
            UniverseEntry(
                ticker=ticker,
                name=_clean(row[name_col]) if name_col else "",
                gics_sector=_clean(row[sector_col]) if sector_col else "",
                gics_sub_industry=_clean(row[sub_col]) if sub_col else "",
                source=source,
            )
        )
    if not entries:
        raise UniverseError(f"Tabela de {source} sem tickers válidos")
    return entries


def parse_wikipedia_html(html: str, source: str) -> list[UniverseEntry]:
    """Extrai os constituintes da primeira tabela com uma coluna de símbolos."""
    tables = pd.read_html(io.StringIO(html))
    for table in tables:
        symbol_col = _find_column(table.columns, SYMBOL_COLUMNS)
        if symbol_col is not None:
            return _entries_from_table(table, source, symbol_col)
    raise UniverseError(f"Nenhuma tabela com coluna de símbolos encontrada na página de {source}")


def fetch_wikipedia_index(index_name: str, timeout: int = 30) -> list[UniverseEntry]:
    url = WIKIPEDIA_SOURCES[index_name]
    log.info("A descarregar constituintes de %s (%s)", index_name, url)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise UniverseError(f"Falha ao obter {index_name} da Wikipedia: {exc}") from exc
    entries = parse_wikipedia_html(response.text, index_name)
    log.info("%s: %d tickers obtidos", index_name, len(entries))
    return entries


def load_csv_universe(path: str | Path, ticker_column: str = "ticker") -> list[UniverseEntry]:
    """Lê uma lista custom de tickers de um CSV (usa a coluna indicada ou a primeira)."""
    path = Path(path)
    if not path.exists():
        raise UniverseError(f"CSV de universo não encontrado: {path}")
    table = pd.read_csv(path)
    if table.empty:
        raise UniverseError(f"CSV de universo vazio: {path}")
    symbol_col = _find_column(table.columns, (ticker_column.lower(), *SYMBOL_COLUMNS)) or table.columns[0]
    return _entries_from_table(table, path.name, symbol_col)


def parse_inline_tickers(text: str) -> list[UniverseEntry]:
    """Converte ``"AAPL, msft;ASML"`` numa lista de entradas sem duplicados."""
    entries: list[UniverseEntry] = []
    seen: set[str] = set()
    for part in text.replace(";", ",").split(","):
        ticker = normalize_ticker(part)
        if ticker and ticker not in seen:
            seen.add(ticker)
            entries.append(UniverseEntry(ticker=ticker, source="inline"))
    if not entries:
        raise UniverseError("Lista de tickers vazia")
    return entries


def get_universe(
    spec: str,
    cache: DiskCache | None = None,
    ttl_hours: float = 168.0,
    refresh: bool = False,
    csv_ticker_column: str = "ticker",
) -> list[UniverseEntry]:
    """Resolve a especificação do universo: ``sp500``, ``sp400``, ``sp600``, ``sp1500`` ou um CSV."""
    key = spec.strip()
    lowered = key.lower()

    if lowered in COMPOSITE_INDICES:
        merged: list[UniverseEntry] = []
        seen: set[str] = set()
        for part in COMPOSITE_INDICES[lowered]:
            for entry in get_universe(part, cache, ttl_hours, refresh, csv_ticker_column):
                if entry.ticker not in seen:
                    seen.add(entry.ticker)
                    merged.append(entry)
        return merged

    if lowered in WIKIPEDIA_SOURCES:
        return _cached_index(lowered, cache, ttl_hours, refresh)

    path = Path(key)
    if path.suffix.lower() in (".csv", ".txt"):
        return load_csv_universe(path, csv_ticker_column)

    raise UniverseError(
        f"Universo desconhecido: '{spec}'. Use sp500, sp400, sp600, sp1500 ou um ficheiro CSV."
    )


def _cached_index(index_name: str, cache: DiskCache | None, ttl_hours: float, refresh: bool) -> list[UniverseEntry]:
    cache_key = f"universe:{index_name}"
    if cache is not None and not refresh:
        payload = cache.get(cache_key, ttl_hours=ttl_hours)
        if payload and payload.get("entries"):
            entries = [UniverseEntry(**item) for item in payload["entries"]]
            log.info("%s: %d tickers (cache)", index_name, len(entries))
            return entries

    try:
        entries = fetch_wikipedia_index(index_name)
    except UniverseError:
        # Sem rede: aceitar uma cópia expirada se existir, avisando.
        if cache is not None:
            payload = cache.get(cache_key, ignore_ttl=True)
            if payload and payload.get("entries"):
                log.warning(
                    "Wikipedia indisponível; a usar a lista de %s em cache (possivelmente desatualizada)",
                    index_name,
                )
                return [UniverseEntry(**item) for item in payload["entries"]]
        raise

    if cache is not None:
        cache.set(cache_key, {"entries": [asdict(entry) for entry in entries]})
    return entries
