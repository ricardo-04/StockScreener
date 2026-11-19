"""Testes de CLI: --preset, --diagnose, --sector, --min-score, --cache-only, --cache-stats, --clear-cache."""
from __future__ import annotations

import pandas as pd
import pytest

import main as cli
from src.universe import UniverseEntry


def test_parse_args_supports_new_flags():
    args = cli.parse_args(
        [
            "--preset", "value",
            "--diagnose", "AAPL,MSFT",
            "--sector", "Technology,Healthcare",
            "--min-score", "65.5",
            "--cache-only",
            "--cache-stats",
            "--clear-cache", "yf",
        ]
    )
    assert args.preset == "value"
    assert args.diagnose == "AAPL,MSFT"
    assert args.sector == "Technology,Healthcare"
    assert args.min_score == 65.5
    assert args.cache_only is True
    assert args.cache_stats is True
    assert args.clear_cache == "yf"

    args_default_clear = cli.parse_args(["--clear-cache"])
    assert args_default_clear.clear_cache == "all"


def test_filter_by_sector_and_min_score():
    df = pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "D"],
            "sector": ["Technology", "Healthcare", "technology", "Energy"],
            "composite_score": [82.0, 75.0, 55.0, float("nan")],
            "rank": [1, 2, 3, 4],
        }
    )
    tech = cli.filter_by_sector(df, "TECHNOLOGY")
    assert list(tech["ticker"]) == ["A", "C"]
    multi = cli.filter_by_sector(df, "Healthcare, Energy")
    assert list(multi["ticker"]) == ["B", "D"]

    high = cli.filter_by_min_score(df, 70.0)
    assert list(high["ticker"]) == ["A", "B"]
    assert list(high["rank"]) == [1, 2]


def test_format_cache_stats():
    text = cli.format_cache_stats(
        {"path": "/tmp/cache.sqlite", "size_bytes": 2048, "total_entries": 12, "by_prefix": {"yf": 10, "universe": 2}}
    )
    assert "/tmp/cache.sqlite" in text
    assert "2.0 KB" in text
    assert "Entradas: 12" in text
    assert "universe=2, yf=10" in text


def test_cache_only_and_refresh_cache_rejected(capsys):
    rc = cli.main(["--cache-only", "--refresh-cache"])
    assert rc == 2
    assert "--cache-only" in capsys.readouterr().err


def test_cache_stats_and_clear_cache_exit_early(monkeypatch, capsys):
    calls: dict[str, list] = {"stats": [], "clear": []}

    class FakeCache:
        def __init__(self, path, ttl_hours=24):
            self.path = path

        def stats(self):
            calls["stats"].append(str(self.path))
            return {"path": str(self.path), "size_bytes": 4096, "total_entries": 5, "by_prefix": {"yf": 5}}

        def clear(self, prefix=None):
            calls["clear"].append(prefix)
            return 5

        def close(self):
            pass

    monkeypatch.setattr(cli, "DiskCache", FakeCache)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **kw: "fake.log")

    assert cli.main(["--cache-stats"]) == 0
    out = capsys.readouterr().out
    assert "Entradas: 5" in out and "yf=5" in out

    assert cli.main(["--clear-cache", "yf"]) == 0
    out = capsys.readouterr().out
    assert "10 entrada(s) removida(s)" in out
    assert calls["clear"] == ["ticker", "yf"]


def test_clear_cache_yf_deletes_real_ticker_prefix_entries(tmp_path, monkeypatch, capsys):
    from src.data.cache import DiskCache as RealDiskCache

    db_path = tmp_path / "yfinance_cache.sqlite"
    seed = RealDiskCache(db_path, ttl_hours=24)
    seed.set("ticker:AAPL", {"info": {}})
    seed.set("ticker:MSFT", {"info": {}})
    seed.set("news:AAPL", {"items": []})
    seed.set("universe:sp500", {"entries": []})
    seed.close()

    monkeypatch.setattr(cli, "DiskCache", lambda path, ttl_hours=24: RealDiskCache(db_path, ttl_hours=ttl_hours))
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **kw: "fake.log")

    assert cli.main(["--clear-cache", "yf", "--cache-stats"]) == 0
    out = capsys.readouterr().out
    assert "2 entrada(s) removida(s)" in out
    assert "Entradas: 2" in out
    assert "news=1, universe=1" in out


def test_main_diagnose_mode_skips_reports_and_prints_all_stages(monkeypatch, capsys):
    metrics = pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "company": ["Apple"],
            "sector": ["Technology"],
            "business_type": ["standard"],
            "market_cap": [1e9],  # falha na etapa 1
            "avg_dollar_volume_60d": [5e7],
            "price": [50.0],
            "years_of_statements": [4],
            "revenue": [1e9], "ebit": [2e8], "net_income": [1.5e8], "operating_cash_flow": [2e8],
            "capex": [-5e7], "total_assets": [2e9], "equity": [9e8], "total_debt": [4e8], "cash": [1e8],
            "net_debt": [3e8], "net_debt_to_ebitda": [1.0], "debt_status": ["com_divida"],
            "interest_coverage": [15.0], "altman_z": [3.0], "current_ratio": [1.5],
            "piotroski_f": [7.0], "piotroski_criteria": [9], "beneish_m": [-2.5],
            "roic_avg_3y": [0.20], "roic_years_observed": [3], "fcf_positive_years": [4],
            "fcf_years_observed": [4], "cash_conversion": [1.0], "gross_margin_std_pp": [2.0],
            "share_dilution_cagr": [-0.01], "revenue_cagr_3y": [0.08], "revenue_cagr_span_years": [3],
            "eps_consecutive_declines": [0.0], "revenue_growth_est_next_fy": [0.05],
            "fcf_yield": [0.05], "ev_to_ebit": [15.0], "forward_pe": [20.0], "peg_ratio": [1.5],
            "price_to_book": [5.0], "dividend_yield": [0.01], "p_ffo": [float("nan")],
            "momentum_12_1": [0.10], "sma_200": [45.0], "price_vs_sma200": [0.11],
        }
    )

    class DummyCache:
        def __init__(self, *a, **kw):
            pass

        def close(self):
            pass

    captured_fetch: dict = {}

    def fake_fetch_many(tickers, cache, settings, refresh=False, cache_only=False):
        captured_fetch["tickers"] = list(tickers)
        captured_fetch["cache_only"] = cache_only
        return {}, []

    monkeypatch.setattr(cli, "DiskCache", DummyCache)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **kw: "fake.log")
    monkeypatch.setattr(cli, "fetch_many", fake_fetch_many)
    monkeypatch.setattr(cli, "build_metrics_table", lambda datas, cfg: (metrics, []))
    monkeypatch.setattr(cli, "export_screener_workbook", lambda *a, **kw: pytest.fail("não deve exportar Excel"))

    rc = cli.main(["--tickers", "AAPL", "--diagnose", "AAPL", "--cache-only", "--preset", "quality"])
    assert rc == 0
    assert captured_fetch == {"tickers": ["AAPL"], "cache_only": True}
    out = capsys.readouterr().out
    assert "DIAGNÓSTICO POR ETAPA" in out
    assert "AAPL (Apple | Technology | standard) — ELIMINADA" in out
    assert "[FALHOU] Etapa 1 – Elegibilidade e dados" in out
    assert "[OK] Etapa 2 – Saúde financeira" in out


def test_main_cache_only_passes_flag_to_get_universe(monkeypatch):
    seen: dict = {}

    class DummyCache:
        def __init__(self, *a, **kw):
            pass

        def close(self):
            pass

    def fake_get_universe(spec, cache, ttl_hours=168, refresh=False, csv_ticker_column="ticker", cache_only=False):
        seen["universe_cache_only"] = cache_only
        return [UniverseEntry(ticker="AAPL", source="sp500")]

    def fake_fetch_many(tickers, cache, settings, refresh=False, cache_only=False):
        seen["fetch_cache_only"] = cache_only
        return {}, []

    monkeypatch.setattr(cli, "DiskCache", DummyCache)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **kw: "fake.log")
    monkeypatch.setattr(cli, "get_universe", fake_get_universe)
    monkeypatch.setattr(cli, "fetch_many", fake_fetch_many)
    monkeypatch.setattr(cli, "build_metrics_table", lambda datas, cfg: (pd.DataFrame(), []))

    rc = cli.main(["--universe", "sp500", "--diagnose", "AAPL", "--cache-only"])
    assert rc == 0
    assert seen == {"universe_cache_only": True, "fetch_cache_only": True}
