"""Descarga de dados do yfinance com retry, backoff exponencial, cache e progresso.

Para cada ticker obtém:
- ``info`` (dados de mercado, múltiplos, setor/indústria);
- demonstrações anuais: resultados, balanço e fluxos de caixa (até N anos);
- histórico diário de preços (1-2 anos);
- estimativas de receita dos analistas (opcional).

Qualquer falha definitiva fica registada em ``FetchFailure`` (ticker + motivo) e
é escrita no log: nada é engolido silenciosamente.
"""
from __future__ import annotations

import inspect
import logging
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from yfinance import exceptions as yf_exc

from src.data.cache import DiskCache

log = logging.getLogger(__name__)

SUPPORTED_QUOTE_TYPES = {"EQUITY"}
MIN_INFO_KEYS = 5
PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
TRANSIENT_EXCEPTION_NAMES = {
    "RequestException", "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout",
    "HTTPError", "CurlError", "SSLError", "ChunkedEncodingError", "RemoteDisconnected",
    "ProtocolError", "JSONDecodeError",
}
TRANSIENT_MESSAGE_HINTS = (
    "timed out", "timeout", "connection", "temporarily", "remote end closed",
    "max retries", "too many requests", "rate limit", "429", "503", "502",
)
# yfinance >= 0.2.x aceita raise_errors em history(); verificado uma vez na importação
_HISTORY_RAISES = "raise_errors" in inspect.signature(yf.Ticker.history).parameters


class FetchError(Exception):
    """Falha na descarga de um ticker (após as tentativas configuradas)."""


class PermanentFetchError(FetchError):
    """Falha que não vale a pena repetir (ticker inválido, instrumento não suportado)."""


@dataclass
class FetchSettings:
    """Parâmetros de download (secção ``data`` do config.yaml)."""

    max_workers: int = 8
    request_delay_seconds: float = 0.15
    retries: int = 3
    backoff_base_seconds: float = 2.0
    rate_limit_backoff_seconds: float = 15.0
    price_history_period: str = "2y"
    statement_years: int = 4
    fetch_estimates: bool = True

    @classmethod
    def from_config(cls, data_cfg: dict[str, Any]) -> "FetchSettings":
        return cls(
            max_workers=int(data_cfg.get("max_workers", 8)),
            request_delay_seconds=float(data_cfg.get("request_delay_seconds", 0.15)),
            retries=int(data_cfg.get("retries", 3)),
            backoff_base_seconds=float(data_cfg.get("backoff_base_seconds", 2.0)),
            rate_limit_backoff_seconds=float(data_cfg.get("rate_limit_backoff_seconds", 15.0)),
            price_history_period=str(data_cfg.get("price_history_period", "2y")),
            statement_years=int(data_cfg.get("statement_years", 4)),
            fetch_estimates=bool(data_cfg.get("fetch_estimates", True)),
        )


@dataclass
class FetchFailure:
    """Ticker que não foi possível descarregar e o motivo (vai para a lista de eliminadas)."""

    ticker: str
    reason: str


@dataclass
class TickerData:
    """Dados brutos de um ticker, tal como vêm do yfinance (ou da cache)."""

    ticker: str
    info: dict[str, Any]
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    prices: pd.DataFrame
    estimates: dict[str, pd.DataFrame] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    from_cache: bool = False

    # ------------------------------------------------------------ serialização
    def to_payload(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "fetched_at": self.fetched_at.isoformat(),
            "info": _sanitize_info(self.info),
            "income": _frame_to_payload(self.income),
            "balance": _frame_to_payload(self.balance),
            "cashflow": _frame_to_payload(self.cashflow),
            "prices": _frame_to_payload(self.prices),
            "estimates": {name: _frame_to_payload(frame) for name, frame in self.estimates.items()},
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TickerData":
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return cls(
            ticker=payload["ticker"],
            info=payload.get("info") or {},
            income=_frame_from_payload(payload.get("income"), datetime_axis="columns"),
            balance=_frame_from_payload(payload.get("balance"), datetime_axis="columns"),
            cashflow=_frame_from_payload(payload.get("cashflow"), datetime_axis="columns"),
            prices=_frame_from_payload(payload.get("prices"), datetime_axis="index"),
            estimates={
                name: _frame_from_payload(frame, datetime_axis=None, numeric=False)
                for name, frame in (payload.get("estimates") or {}).items()
            },
            warnings=list(payload.get("warnings") or []),
            fetched_at=fetched_at,
            from_cache=True,
        )


# ---------------------------------------------------------------------------
# Serialização JSON de DataFrames (sem pickle: a cache é inspecionável e estável)
# ---------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if hasattr(value, "item"):  # escalares numpy
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _frame_to_payload(frame: pd.DataFrame | None) -> dict[str, Any] | None:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    return {
        "index": [str(idx) for idx in frame.index],
        "columns": [str(col) for col in frame.columns],
        "data": [[_json_safe(v) for v in row] for row in frame.itertuples(index=False, name=None)],
    }


def _frame_from_payload(
    payload: dict[str, Any] | None, datetime_axis: str | None, numeric: bool = True
) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame()
    frame = pd.DataFrame(payload["data"], index=payload["index"], columns=payload["columns"])
    if numeric:
        frame = frame.apply(pd.to_numeric, errors="coerce")
    if datetime_axis == "columns":
        frame.columns = pd.to_datetime(frame.columns, errors="coerce")
    elif datetime_axis == "index":
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        frame.index.name = "date"
    return frame


def _sanitize_info(info: dict[str, Any]) -> dict[str, Any]:
    """Mantém apenas escalares JSON (descarta listas aninhadas como companyOfficers)."""
    clean: dict[str, Any] = {}
    for key, value in (info or {}).items():
        if isinstance(value, (bool, int, float, str)) or value is None:
            clean[key] = _json_safe(value)
    return clean


# ---------------------------------------------------------------------------
# Download de um ticker
# ---------------------------------------------------------------------------
def _is_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, yf_exc.YFRateLimitError):
        return True
    message = str(exc).lower()
    return "too many requests" in message or "rate limit" in message or "429" in message


def _is_transient(exc: BaseException) -> bool:
    if _is_rate_limited(exc):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if type(exc).__name__ in TRANSIENT_EXCEPTION_NAMES:
        return True
    message = str(exc).lower()
    return any(hint in message for hint in TRANSIENT_MESSAGE_HINTS)


def _short(exc: BaseException, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _statement(ticker_obj: yf.Ticker, attribute: str, years: int, warnings: list[str]) -> pd.DataFrame:
    """Demonstração anual ordenada do ano mais recente para o mais antigo, limitada a ``years``."""
    try:
        frame = getattr(ticker_obj, attribute)
    except Exception as exc:  # noqa: BLE001 - decidimos abaixo se é transitório
        if _is_transient(exc):
            raise
        warnings.append(f"{attribute} indisponível ({type(exc).__name__}: {_short(exc)})")
        return pd.DataFrame()
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        warnings.append(f"{attribute} vazio")
        return pd.DataFrame()
    frame = frame.copy()
    try:
        frame = frame[sorted(frame.columns, reverse=True)]
    except TypeError:
        pass
    return frame.iloc[:, :years]


def _prices(ticker_obj: yf.Ticker, settings: FetchSettings, warnings: list[str]) -> pd.DataFrame:
    kwargs: dict[str, Any] = {
        "period": settings.price_history_period,
        "interval": "1d",
        "auto_adjust": True,
    }
    if _HISTORY_RAISES:
        kwargs["raise_errors"] = True
    try:
        frame = ticker_obj.history(**kwargs)
    except (yf_exc.YFPricesMissingError, yf_exc.YFTzMissingError, yf_exc.YFInvalidPeriodError) as exc:
        warnings.append(f"histórico de preços indisponível ({_short(exc)})")
        return pd.DataFrame()
    if frame is None or frame.empty:
        warnings.append("histórico de preços vazio")
        return pd.DataFrame()
    columns = [c for c in PRICE_COLUMNS if c in frame.columns]
    frame = frame[columns].copy()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_localize(None)
    frame.index.name = "date"
    return frame


def _estimates(ticker_obj: yf.Ticker, warnings: list[str]) -> dict[str, pd.DataFrame]:
    estimates: dict[str, pd.DataFrame] = {}
    try:
        revenue_estimate = ticker_obj.revenue_estimate
    except Exception as exc:  # noqa: BLE001
        if _is_transient(exc):
            raise
        warnings.append(f"estimativas de receita indisponíveis ({type(exc).__name__})")
        return estimates
    if isinstance(revenue_estimate, pd.DataFrame) and not revenue_estimate.empty:
        estimates["revenue_estimate"] = revenue_estimate.copy()
    return estimates


def _download(ticker: str, settings: FetchSettings) -> TickerData:
    ticker_obj = yf.Ticker(ticker)
    info = ticker_obj.info
    if not isinstance(info, dict) or len(info) < MIN_INFO_KEYS:
        raise PermanentFetchError("info vazio: ticker inválido ou sem dados no Yahoo Finance")
    quote_type = info.get("quoteType")
    if quote_type and quote_type not in SUPPORTED_QUOTE_TYPES:
        raise PermanentFetchError(f"tipo de instrumento não suportado: {quote_type}")
    if info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        raise PermanentFetchError("sem preço de mercado em info")

    warnings: list[str] = []
    income = _statement(ticker_obj, "income_stmt", settings.statement_years, warnings)
    balance = _statement(ticker_obj, "balance_sheet", settings.statement_years, warnings)
    cashflow = _statement(ticker_obj, "cashflow", settings.statement_years, warnings)
    prices = _prices(ticker_obj, settings, warnings)
    estimates = _estimates(ticker_obj, warnings) if settings.fetch_estimates else {}

    return TickerData(
        ticker=ticker,
        info=_sanitize_info(info),
        income=income,
        balance=balance,
        cashflow=cashflow,
        prices=prices,
        estimates=estimates,
        warnings=warnings,
    )


def _backoff_delay(exc: BaseException, attempt: int, settings: FetchSettings) -> float:
    base = settings.rate_limit_backoff_seconds if _is_rate_limited(exc) else settings.backoff_base_seconds
    jitter = random.uniform(0.0, 1.0)
    return base * (2 ** (attempt - 1)) + jitter


def fetch_ticker(ticker: str, settings: FetchSettings) -> TickerData:
    """Descarrega um ticker com retry e backoff exponencial. Lança ``FetchError`` se esgotar."""
    attempts = settings.retries + 1
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _download(ticker, settings)
        except PermanentFetchError:
            raise
        except Exception as exc:  # noqa: BLE001 - qualquer outra falha é candidata a retry
            last_exc = exc
            if attempt >= attempts:
                break
            delay = _backoff_delay(exc, attempt, settings)
            log.warning(
                "%s: tentativa %d/%d falhou (%s: %s); nova tentativa em %.1fs",
                ticker, attempt, attempts, type(exc).__name__, _short(exc), delay,
            )
            time.sleep(delay)
    raise FetchError(
        f"falhou após {attempts} tentativas ({type(last_exc).__name__}: {_short(last_exc)})"
    ) from last_exc


# ---------------------------------------------------------------------------
# Download concorrente com cache
# ---------------------------------------------------------------------------
def fetch_many(
    tickers: list[str],
    cache: DiskCache | None,
    settings: FetchSettings,
    refresh: bool = False,
) -> tuple[list[TickerData], list[FetchFailure]]:
    """Descarrega vários tickers em paralelo (ThreadPoolExecutor + tqdm).

    Devolve (dados obtidos, falhas). Entradas de cache válidas evitam pedidos ao Yahoo.
    """

    def task(ticker: str) -> TickerData:
        cache_key = f"ticker:{ticker}"
        if cache is not None and not refresh:
            payload = cache.get(cache_key)
            if payload:
                try:
                    return TickerData.from_payload(payload)
                except Exception as exc:  # noqa: BLE001 - cache corrompida -> novo download
                    log.warning("%s: entrada de cache inválida (%s); a descarregar de novo", ticker, _short(exc))
        if settings.request_delay_seconds > 0:
            time.sleep(settings.request_delay_seconds)
        data = fetch_ticker(ticker, settings)
        if cache is not None:
            cache.set(cache_key, data.to_payload())
        return data

    results: dict[str, TickerData] = {}
    failures: list[FetchFailure] = []
    workers = max(1, min(settings.max_workers, len(tickers) or 1))
    log.info("A descarregar %d tickers com %d workers (cache %s)", len(tickers), workers,
             "ignorada" if refresh else "ativa")

    with logging_redirect_tqdm():
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(task, ticker): ticker for ticker in tickers}
            progress = tqdm(as_completed(futures), total=len(futures), desc="Download", unit="ticker")
            for future in progress:
                ticker = futures[future]
                try:
                    results[ticker] = future.result()
                except Exception as exc:  # noqa: BLE001 - registar sempre ticker + motivo
                    reason = _short(exc, limit=300)
                    log.error("%s: eliminado no download: %s", ticker, reason)
                    failures.append(FetchFailure(ticker=ticker, reason=reason))

    ordered = [results[t] for t in tickers if t in results]
    from_cache = sum(1 for d in ordered if d.from_cache)
    log.info(
        "Download concluído: %d ok (%d da cache, %d novos), %d falhas",
        len(ordered), from_cache, len(ordered) - from_cache, len(failures),
    )
    return ordered, failures
