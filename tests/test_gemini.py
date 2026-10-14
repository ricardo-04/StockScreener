"""Analista Gemini com cliente simulado: prompt, parsing/validação, cache, retry, red flags."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from src.ai import gemini_analyst as ga
from src.ai.gemini_analyst import (
    INSUFFICIENT, REVIEW_FLAG, AiResult, GeminiAnalyst, ai_columns, build_prompt, parse_analysis,
)
from src.data.cache import DiskCache
from src.news import NewsItem

COMPANY = {
    "ticker": "ACN", "company": "Accenture plc", "sector": "Technology", "industry": "IT Services",
    "business_type": "standard", "price": 216.5, "market_cap": 132.5e9, "composite_score": 59.0,
    "roic_avg_3y": 0.32, "fcf_yield": 0.084, "net_debt_to_ebitda": float("nan"), "debt_status": "com_divida",
    "forward_pe": 17.2, "piotroski_f": 7.0, "funnel_warnings": "",
}
NEWS = [
    NewsItem("Accenture wins AI contract", "Reuters", datetime(2026, 10, 10, tzinfo=timezone.utc), "https://x/1", "Contrato grande."),
    NewsItem("CFO steps down", "Bloomberg", datetime(2026, 10, 9, tzinfo=timezone.utc), "https://x/2", ""),
]
GOOD_RESPONSE = {
    "sentiment": "positivo", "sentiment_score": 0.6,
    "catalysts": ["Contrato de IA [N1]"], "risks": ["Saída do CFO [N2]"], "red_flags": [],
    "moat_assessment": "Escala e relações com clientes (métricas: ROIC 32%).",
    "thesis_summary": "Frase 1. Frase 2. Frase 3.", "news_impact": "Positivo a curto prazo [N1].",
}


def test_build_prompt_contains_metrics_explanation_and_numbered_news():
    prompt = build_prompt(COMPANY, "Passou todas as 6 etapas.", NEWS)
    assert "Accenture plc (ACN)" in prompt
    assert "- ROIC médio 3 anos: 32.0%" in prompt
    assert "- FCF yield (FCF/EV): 8.4%" in prompt
    assert "- Market cap: 132.50 mil M" in prompt
    assert "Net Debt/EBITDA" not in prompt  # NaN não é enviado
    assert "- Situação de dívida: com_divida" in prompt
    assert "Passou todas as 6 etapas." in prompt
    assert "[N1] 2026-10-10 | Reuters | Accenture wins AI contract — Contrato grande." in prompt
    assert "[N2] 2026-10-09 | Bloomberg | CFO steps down" in prompt
    assert "## Notícias recentes (2)" in prompt
    empty = build_prompt(COMPANY, "", [])
    assert INSUFFICIENT in empty and "(sem notícias disponíveis" in empty
    as_dicts = build_prompt(COMPANY, "", [n.to_dict() for n in NEWS])
    assert "[N2]" in as_dicts


def test_parse_analysis_normalizes_and_validates():
    parsed = parse_analysis(json.dumps(GOOD_RESPONSE))
    assert parsed["sentiment"] == "positivo" and parsed["sentiment_score"] == 0.6
    assert parsed["catalysts"] == ["Contrato de IA [N1]"]

    loose = dict(GOOD_RESPONSE, sentiment="BULLISH", sentiment_score=1.7, red_flags="não é lista", catalysts=[" a ", "", None])
    parsed = parse_analysis(json.dumps(loose))
    assert parsed["sentiment"] == "positivo" and parsed["sentiment_score"] == 1.0
    assert parsed["red_flags"] == [] and parsed["catalysts"] == ["a"]

    no_score = dict(GOOD_RESPONSE, sentiment="negativo", sentiment_score=None, thesis_summary="")
    parsed = parse_analysis(json.dumps(no_score))
    assert parsed["sentiment_score"] == -0.5 and parsed["thesis_summary"] == INSUFFICIENT

    unknown_both = dict(GOOD_RESPONSE, sentiment="", sentiment_score=-0.9)
    assert parse_analysis(json.dumps(unknown_both))["sentiment"] == "negativo"

    with pytest.raises(ValueError):
        parse_analysis("isto não é json")
    with pytest.raises(ValueError):
        parse_analysis(json.dumps([1, 2]))


def make_analyst(tmp_path, responses, cfg=None):
    calls = []

    def fake_generate(system_instruction, prompt):
        calls.append(prompt)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response)

    cfg = cfg or {"ai": {"model": "fake-model", "rate_limit_seconds": 0, "retries": 2, "backoff_base_seconds": 0}}
    cache = DiskCache(tmp_path / "ai.sqlite")
    return GeminiAnalyst(cfg, api_key=None, cache=cache, generate=fake_generate), calls, cache


def test_analyze_ok_and_cached(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    analyst, calls, cache = make_analyst(tmp_path, [GOOD_RESPONSE])
    assert analyst.available
    result = analyst.analyze(COMPANY, "Explicação", NEWS)
    assert result.ok and result.model == "fake-model" and not result.review_required and not result.from_cache
    assert result.analysis["sentiment"] == "positivo"
    assert len(calls) == 1 and "[N1]" in calls[0]

    again = analyst.analyze(COMPANY, "Explicação", NEWS)
    assert again.from_cache and again.analysis == result.analysis and len(calls) == 1
    columns = ai_columns(again)
    assert columns["ai_sentiment"] == "positivo" and columns["ai_review"] == "" and columns["ai_from_cache"] is True
    assert columns["ai_risks"] == "Saída do CFO [N2]"
    cache.close()


def test_red_flags_mark_manual_review(tmp_path):
    flagged = dict(GOOD_RESPONSE, sentiment="negativo", sentiment_score=-0.4, red_flags=["Investigação regulatória [N2]"])
    analyst, _, cache = make_analyst(tmp_path, [flagged])
    result = analyst.analyze(COMPANY, "", NEWS)
    assert result.review_required
    columns = ai_columns(result)
    assert columns["ai_review"] == REVIEW_FLAG and columns["ai_red_flags"] == "Investigação regulatória [N2]"
    cache.close()


def test_errors_do_not_stop_pipeline_and_are_not_cached(tmp_path):
    analyst, calls, cache = make_analyst(tmp_path, [ValueError("resposta bloqueada por segurança"), GOOD_RESPONSE])
    result = analyst.analyze(COMPANY, "", NEWS)
    assert not result.ok and "ValueError" in result.error and result.analysis is None
    assert ai_columns(result)["ai_error"].startswith("ValueError")
    assert ai_columns(result)["ai_sentiment"] == "" and math.isnan(ai_columns(result)["ai_sentiment_score"])
    retry = analyst.analyze(COMPANY, "", NEWS)  # o erro não ficou em cache: nova chamada
    assert retry.ok and len(calls) == 2
    cache.close()


def test_transient_errors_are_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(ga.time, "sleep", lambda *_: None)
    analyst, calls, cache = make_analyst(tmp_path, [RuntimeError("429 RESOURCE_EXHAUSTED"), GOOD_RESPONSE])
    result = analyst.analyze(COMPANY, "", NEWS)
    assert result.ok and len(calls) == 2
    cache.close()


def test_unavailable_model_switches_to_api_suggestion(tmp_path):
    analyst, calls, cache = make_analyst(tmp_path, [])
    state = {"model_at_call": []}

    def generate(system_instruction, prompt):
        state["model_at_call"].append(analyst.model)
        if analyst.model == "fake-model":
            raise RuntimeError("404 NOT_FOUND. This model models/fake-model is no longer available. "
                               "Please update your code to use models/novo-flash for the latest features")
        return json.dumps(GOOD_RESPONSE)

    analyst._generate = generate
    assert analyst.suggested_model("use models/novo-flash") == "novo-flash"
    assert analyst.suggested_model("models/fake-model only") is None
    result = analyst.analyze(COMPANY, "", NEWS)
    assert result.ok and result.model == "novo-flash"
    assert state["model_at_call"] == ["fake-model", "novo-flash"]
    # a troca só acontece uma vez: um novo 404 com sugestão é reportado como erro
    analyst.model = "fake-model"
    result = analyst.analyze(dict(COMPANY, ticker="X"), "", NEWS)
    assert not result.ok and "404" in result.error
    cache.close()


def test_overloaded_model_falls_back_to_alternatives(tmp_path, monkeypatch):
    monkeypatch.setattr(ga.time, "sleep", lambda *_: None)
    cfg = {"ai": {"model": "principal", "fallback_models": ["alt-1", "alt-2"], "rate_limit_seconds": 0, "retries": 1,
                  "backoff_base_seconds": 0}}
    models_used: list[str] = []

    def generate(system_instruction, prompt):
        models_used.append(analyst.model)
        if analyst.model in ("principal", "alt-1"):
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return json.dumps(GOOD_RESPONSE)

    analyst = GeminiAnalyst(cfg, api_key=None, cache=None, generate=generate)
    result = analyst.analyze(COMPANY, "", NEWS)
    assert result.ok and result.model == "alt-2"
    assert models_used == ["principal", "principal", "alt-1", "alt-1", "alt-2"]  # 2 tentativas por modelo
    second = analyst.analyze(dict(COMPANY, ticker="MSFT"), "", NEWS)  # já usa diretamente o alternativo
    assert second.ok and models_used[-1] == "alt-2" and len(models_used) == 6

    # análise guardada sob o modelo alternativo é reutilizada por um novo analista com o modelo principal
    cache = DiskCache(tmp_path / "ai.sqlite")
    cached_analyst = GeminiAnalyst(cfg, api_key=None, cache=cache, generate=generate)
    cached_analyst.model = "alt-2"
    cached_analyst.fallback_models = []
    assert cached_analyst.analyze(COMPANY, "", NEWS).ok
    fresh = GeminiAnalyst(cfg, api_key=None, cache=cache, generate=generate)
    hit = fresh.analyze(COMPANY, "", NEWS)
    assert hit.from_cache and hit.model == "alt-2" and fresh.model == "principal"
    cache.close()
    # erro não transitório não muda de modelo
    broken = GeminiAnalyst(cfg, api_key=None, generate=lambda s, p: (_ for _ in ()).throw(ValueError("400 bad request")))
    assert not broken.analyze(COMPANY, "", NEWS).ok and broken.model == "principal"


def test_quota_exhausted_switches_model_without_retrying(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(ga.time, "sleep", lambda s: sleeps.append(s))
    cfg = {"ai": {"model": "principal", "fallback_models": ["alt"], "rate_limit_seconds": 0, "retries": 3, "backoff_base_seconds": 5}}
    calls: list[str] = []

    def generate(system_instruction, prompt):
        calls.append(analyst.model)
        if analyst.model == "principal":
            raise RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota, please check your plan")
        return json.dumps(GOOD_RESPONSE)

    analyst = GeminiAnalyst(cfg, api_key=None, generate=generate)
    result = analyst.analyze(COMPANY, "", NEWS)
    assert result.ok and result.model == "alt"
    assert calls == ["principal", "alt"]  # sem repetições no modelo com quota esgotada
    assert sleeps == []


def test_invalid_argument_retries_without_thinking(tmp_path):
    cfg = {"ai": {"model": "lite", "thinking_budget": 0, "rate_limit_seconds": 0, "retries": 0}}
    seen: list[object] = []

    def generate(system_instruction, prompt):
        seen.append(analyst.thinking_budget)
        if analyst.thinking_budget is not None:
            raise RuntimeError("400 INVALID_ARGUMENT. Request contains an invalid argument.")
        return json.dumps(GOOD_RESPONSE)

    analyst = GeminiAnalyst(cfg, api_key=None, generate=generate)
    assert analyst.analyze(COMPANY, "", NEWS).ok
    assert seen == [0, None]
    assert analyst.analyze(dict(COMPANY, ticker="B"), "", NEWS).ok and seen == [0, None, None]


def test_invalid_json_from_model_is_reported(tmp_path):
    def bad_generate(system_instruction, prompt):
        return "não é json"

    analyst = GeminiAnalyst({"ai": {"rate_limit_seconds": 0, "retries": 0}}, api_key=None, generate=bad_generate)
    result = analyst.analyze(COMPANY, "", [])
    assert not result.ok and "JSON" in result.error


def test_analyze_many_and_availability(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert not GeminiAnalyst({"ai": {}}, api_key=None).available
    assert GeminiAnalyst({"ai": {}}, api_key="chave").available
    monkeypatch.setenv("GEMINI_MODEL", "modelo-env")
    assert GeminiAnalyst({"ai": {"model": "x"}}, api_key="chave").model == "modelo-env"
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    analyst, calls, cache = make_analyst(tmp_path, [GOOD_RESPONSE, dict(GOOD_RESPONSE, red_flags=["Processo judicial [N1]"])])
    other = dict(COMPANY, ticker="MSFT", company="Microsoft")
    results = analyst.analyze_many([(COMPANY, "e1", NEWS), (other, "e2", [])])
    assert list(results) == ["ACN", "MSFT"]
    assert not results["ACN"].review_required and results["MSFT"].review_required
    cache.close()


def test_ai_result_payload_roundtrip():
    result = AiResult(ticker="ACN", model="m", analysis=GOOD_RESPONSE, review_required=False)
    restored = AiResult.from_payload(result.to_payload())
    assert restored.from_cache and restored.analysis == GOOD_RESPONSE and restored.ticker == "ACN"
    assert ai_columns(None)["ai_sentiment"] == ""
