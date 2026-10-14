"""Análise qualitativa com Gemini (SDK oficial ``google-genai``), só para o Top N.

Para cada empresa envia as métricas principais, a explicação do funil e as
notícias, e pede JSON estrito (structured output) com sentimento, catalisadores,
riscos, red flags, moat, tese e impacto das notícias.

Regras:
- a IA NÃO altera a aprovação do funil; red flags marcam "REVER MANUALMENTE";
- a instrução de sistema obriga a usar APENAS as notícias e métricas fornecidas,
  a citar a notícia que suporta cada risco/catalisador ([N3]) e a dizer
  "informação insuficiente" quando for o caso;
- cache das respostas por (modelo, ticker, data) e rate limiting simples;
- sem chave (``GEMINI_API_KEY`` no .env) ou com ``--no-ai`` o pipeline corre sem esta etapa.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from src.data.cache import DiskCache
from src.metrics.statements import to_float
from src.news import NewsItem

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"
SENTIMENTS = ("positivo", "neutro", "negativo")
INSUFFICIENT = "informação insuficiente"
REVIEW_FLAG = "REVER MANUALMENTE"
RETRYABLE_CODES = {429, 500, 502, 503, 504}
RETRYABLE_HINTS = ("429", "resource_exhausted", "503", "unavailable", "overloaded", "timed out", "timeout", "deadline")
MODEL_UNAVAILABLE_HINTS = ("404", "not found", "no longer available", "is not supported", "not available")
MODEL_SUGGESTION = re.compile(r"models/([A-Za-z0-9._-]+)")

SYSTEM_INSTRUCTION = f"""És um analista de equity research sénior. Recebes, sobre UMA empresa: métricas quantitativas
(fonte: Yahoo Finance), a explicação gerada por um screener em funil e uma lista numerada de notícias recentes.

Regras obrigatórias:
1. Baseia-te APENAS nas notícias e nas métricas fornecidas. Não uses conhecimento externo nem inventes factos,
   números, datas ou eventos.
2. Para cada catalisador, risco e red flag cita a notícia que o suporta com o seu identificador entre parênteses
   retos, por exemplo "[N3]". Um ponto sem notícia de suporte só pode vir das métricas e deve dizê-lo ("métricas").
3. Red flags são apenas factos graves presentes nas notícias: investigação regulatória, processo judicial relevante,
   saída de CEO/CFO, profit warning, restatement de contas, fraude, suspensão de dividendo, going concern.
   Se não existirem, devolve a lista vazia.
4. Quando as notícias não permitirem concluir, escreve exatamente "{INSUFFICIENT}" nesse campo.
5. Não dás recomendações de compra ou venda e não alteras a aprovação do funil; descreves evidência.
6. Responde em português de Portugal, em JSON estrito segundo o schema fornecido, sem texto fora do JSON.
   sentiment_score vai de -1.0 (muito negativo) a 1.0 (muito positivo) e tem de ser coerente com sentiment.
   thesis_summary tem 3 a 4 frases."""


class GeminiAnalysis(BaseModel):
    """Schema da resposta (structured output)."""

    sentiment: Literal["positivo", "neutro", "negativo"] = Field(description="Sentimento global das notícias e métricas")
    sentiment_score: float = Field(description="-1.0 (muito negativo) a 1.0 (muito positivo)")
    catalysts: list[str] = Field(description="Catalisadores curtos, cada um com a notícia de suporte [Nx]")
    risks: list[str] = Field(description="Riscos curtos, cada um com a notícia de suporte [Nx]")
    red_flags: list[str] = Field(description="Red flags graves com a notícia de suporte [Nx]; lista vazia se não houver")
    moat_assessment: str = Field(description="Avaliação curta da vantagem competitiva, com base nas métricas e notícias")
    thesis_summary: str = Field(description="Resumo da tese em 3 a 4 frases")
    news_impact: str = Field(description="Como as notícias podem afetar o preço a curto/médio prazo")


# Métricas enviadas no prompt: (coluna, rótulo, formato)
PROMPT_METRICS: list[tuple[str, str, str]] = [
    ("price", "Preço", "price"),
    ("market_cap", "Market cap", "money"),
    ("composite_score", "Score do funil (0-100, 50 = mediana do setor)", "num"),
    ("roic_avg_3y", "ROIC médio 3 anos", "pct"),
    ("roe_avg_3y", "ROE médio 3 anos", "pct"),
    ("gross_margin", "Margem bruta", "pct"),
    ("operating_margin", "Margem operacional", "pct"),
    ("revenue_cagr_3y", "CAGR da receita 3 anos", "pct"),
    ("revenue_growth_est_next_fy", "Crescimento de receita esperado (próximo ano fiscal)", "pct"),
    ("free_cash_flow", "FCF último ano", "money"),
    ("fcf_yield", "FCF yield (FCF/EV)", "pct"),
    ("cash_conversion", "Conversão de caixa (FCF/lucro)", "x"),
    ("net_debt_to_ebitda", "Net Debt/EBITDA", "x"),
    ("interest_coverage", "Cobertura de juros", "x"),
    ("debt_status", "Situação de dívida", "text"),
    ("forward_pe", "Forward P/E", "x"),
    ("ev_to_ebit", "EV/EBIT", "x"),
    ("peg_ratio", "PEG", "num"),
    ("price_to_book", "P/B", "x"),
    ("dividend_yield", "Dividend yield", "pct"),
    ("piotroski_f", "Piotroski F-Score", "int"),
    ("altman_z", "Altman Z-Score", "num"),
    ("beneish_m", "Beneish M-Score", "num"),
    ("momentum_12_1", "Momentum 12-1 meses", "pct"),
    ("share_dilution_cagr", "Variação anual do nº de ações", "pct"),
]


def _format(value: Any, kind: str) -> str | None:
    if kind == "text":
        text = "" if value is None else str(value).strip()
        return text or None
    number = to_float(value)
    if math.isnan(number):
        return None
    if kind == "pct":
        return f"{number:.1%}"
    if kind == "x":
        return f"{number:.1f}x"
    if kind == "int":
        return f"{number:.0f}"
    if kind == "price":
        return f"{number:,.2f}"
    if kind == "money":
        return f"{number / 1e9:,.2f} mil M"
    return f"{number:.2f}"


def build_prompt(company: dict[str, Any], explanation: str, news: list[NewsItem | dict[str, Any]]) -> str:
    """Texto enviado ao modelo: identificação, métricas, explicação do funil e notícias numeradas."""
    items = [it if isinstance(it, NewsItem) else NewsItem.from_dict(it) for it in news]
    lines = [
        f"## Empresa: {company.get('company') or company.get('ticker')} ({company.get('ticker')})",
        f"Setor: {company.get('sector') or 'n/d'} | Indústria: {company.get('industry') or 'n/d'} | "
        f"Tipo de negócio no screener: {company.get('business_type') or 'standard'}",
        "",
        "## Métricas quantitativas (Yahoo Finance; último ano fiscal e médias de 3 anos)",
    ]
    for column, label, kind in PROMPT_METRICS:
        text = _format(company.get(column), kind)
        if text is not None:
            lines.append(f"- {label}: {text}")
    lines += ["", "## Explicação do screener (regras, sem IA)", explanation.strip() or "n/d"]
    warnings = str(company.get("funnel_warnings") or "").strip()
    if warnings and warnings.lower() != "nan":
        lines += ["", f"Alertas do funil: {warnings}"]
    lines += ["", f"## Notícias recentes ({len(items)})"]
    if not items:
        lines.append(f"(sem notícias disponíveis: usa '{INSUFFICIENT}' nos campos que dependam delas)")
    for i, item in enumerate(items, start=1):
        summary = f" — {item.summary}" if item.summary else ""
        lines.append(f"[N{i}] {item.published_text} | {item.source or 'fonte n/d'} | {item.title}{summary}")
    lines += [
        "",
        "## Tarefa",
        "Analisa a empresa segundo as regras da instrução de sistema e devolve apenas o JSON pedido.",
    ]
    return "\n".join(lines)


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def parse_analysis(text: str) -> dict[str, Any]:
    """Converte a resposta JSON num dicionário validado e normalizado. Lança ValueError se inválida."""
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"resposta não é JSON válido: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("resposta JSON não é um objeto")

    score = to_float(raw.get("sentiment_score"))
    sentiment = str(raw.get("sentiment") or "").strip().lower()
    if sentiment not in SENTIMENTS:
        sentiment = "neutro" if math.isnan(score) else ("positivo" if score > 0.2 else "negativo" if score < -0.2 else "neutro")
    if math.isnan(score):
        score = {"positivo": 0.5, "neutro": 0.0, "negativo": -0.5}[sentiment]
    score = max(-1.0, min(1.0, score))

    normalized = {
        "sentiment": sentiment,
        "sentiment_score": round(score, 3),
        "catalysts": _clean_list(raw.get("catalysts")),
        "risks": _clean_list(raw.get("risks")),
        "red_flags": _clean_list(raw.get("red_flags")),
        "moat_assessment": str(raw.get("moat_assessment") or INSUFFICIENT).strip(),
        "thesis_summary": str(raw.get("thesis_summary") or INSUFFICIENT).strip(),
        "news_impact": str(raw.get("news_impact") or INSUFFICIENT).strip(),
    }
    try:
        GeminiAnalysis.model_validate(normalized)
    except ValidationError as exc:
        raise ValueError(f"resposta não respeita o schema: {exc}") from exc
    return normalized


@dataclass
class AiResult:
    ticker: str
    model: str
    analysis: dict[str, Any] | None
    review_required: bool = False
    from_cache: bool = False
    error: str | None = None
    analyzed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return self.analysis is not None and self.error is None

    def to_payload(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "model": self.model, "analysis": self.analysis,
            "review_required": self.review_required, "analyzed_at": self.analyzed_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AiResult":
        return cls(
            ticker=str(payload["ticker"]), model=str(payload.get("model") or ""),
            analysis=payload.get("analysis"), review_required=bool(payload.get("review_required")),
            from_cache=True, analyzed_at=str(payload.get("analyzed_at") or ""),
        )


def ai_columns(result: AiResult | None) -> dict[str, Any]:
    """Colunas para o DataFrame do Top N (vazias quando não há análise)."""
    columns: dict[str, Any] = {
        "ai_sentiment": "", "ai_sentiment_score": float("nan"), "ai_review": "", "ai_red_flags": "",
        "ai_catalysts": "", "ai_risks": "", "ai_moat_assessment": "", "ai_thesis_summary": "",
        "ai_news_impact": "", "ai_model": "", "ai_error": "", "ai_from_cache": False,
    }
    if result is None:
        return columns
    columns["ai_model"] = result.model
    columns["ai_from_cache"] = result.from_cache
    columns["ai_error"] = result.error or ""
    if not result.ok:
        return columns
    analysis = result.analysis or {}
    columns.update({
        "ai_sentiment": analysis.get("sentiment", ""),
        "ai_sentiment_score": to_float(analysis.get("sentiment_score")),
        "ai_review": REVIEW_FLAG if result.review_required else "",
        "ai_red_flags": " | ".join(analysis.get("red_flags") or []),
        "ai_catalysts": " | ".join(analysis.get("catalysts") or []),
        "ai_risks": " | ".join(analysis.get("risks") or []),
        "ai_moat_assessment": analysis.get("moat_assessment", ""),
        "ai_thesis_summary": analysis.get("thesis_summary", ""),
        "ai_news_impact": analysis.get("news_impact", ""),
    })
    return columns


def _error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _is_quota_exhausted(exc: BaseException) -> bool:
    """429 de quota esgotada (não de ritmo): repetir não ajuda, mudar de modelo pode."""
    message = str(exc).lower()
    return ("429" in message or _error_code(exc) == 429) and "quota" in message


def _is_retryable(exc: BaseException) -> bool:
    if _is_quota_exhausted(exc):
        return False
    code = _error_code(exc)
    if code in RETRYABLE_CODES:
        return True
    message = str(exc).lower()
    return any(hint in message for hint in RETRYABLE_HINTS)


def _is_invalid_argument(exc: BaseException) -> bool:
    message = str(exc).lower()
    return _error_code(exc) == 400 or "400" in message or "invalid argument" in message or "invalid_argument" in message


class GeminiAnalyst:
    """Cliente de análise com cache, rate limiting e retry. ``generate`` é injetável para testes."""

    def __init__(
        self,
        cfg: dict[str, Any],
        api_key: str | None = None,
        cache: DiskCache | None = None,
        generate: Callable[[str, str], str] | None = None,
    ) -> None:
        ai_cfg = cfg.get("ai", {}) or {}
        self.model = os.environ.get("GEMINI_MODEL") or str(ai_cfg.get("model") or DEFAULT_MODEL)
        self.fallback_models = [str(m) for m in (ai_cfg.get("fallback_models") or []) if m and str(m) != self.model]
        self.api_key = api_key if api_key is not None else (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self.rate_limit_seconds = float(ai_cfg.get("rate_limit_seconds", 1.0))
        self.retries = int(ai_cfg.get("retries", 3))
        self.backoff_base_seconds = float(ai_cfg.get("backoff_base_seconds", 5.0))
        self.temperature = float(ai_cfg.get("temperature", 0.2))
        self.max_output_tokens = int(ai_cfg.get("max_output_tokens", 2048))
        self.thinking_budget = ai_cfg.get("thinking_budget", 0)
        self.cache = cache
        self.cache_ttl_hours = float(ai_cfg.get("cache_ttl_hours", 720))
        self._generate = generate
        self._client: Any = None
        self._last_call = 0.0
        self._model_switched = False

    # ------------------------------------------------------------------ estado
    @property
    def available(self) -> bool:
        return self._generate is not None or bool(self.api_key)

    # ------------------------------------------------------------------ chamada ao SDK
    def _client_generate(self, system_instruction: str, prompt: str, use_thinking: bool = True) -> str:
        from google import genai  # import tardio: só quando há chave
        from google.genai import types

        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        config_kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "response_schema": GeminiAnalysis,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        if use_thinking and self.thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=int(self.thinking_budget))
        response = self._client.models.generate_content(
            model=self.model, contents=prompt, config=types.GenerateContentConfig(**config_kwargs)
        )
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("resposta vazia do modelo (possível bloqueio de segurança ou truncatura)")
        return text

    def _raw_generate(self, system_instruction: str, prompt: str, use_thinking: bool) -> str:
        if self._generate is not None:
            return self._generate(system_instruction, prompt)
        return self._client_generate(system_instruction, prompt, use_thinking)

    def suggested_model(self, message: str) -> str | None:
        """Nome de modelo sugerido numa mensagem de erro da API ("use models/<nome>"), se diferente do atual."""
        for candidate in MODEL_SUGGESTION.findall(message or ""):
            if candidate != self.model:
                return candidate
        return None

    def _generate_text(self, system_instruction: str, prompt: str) -> str:
        try:
            return self._raw_generate(system_instruction, prompt, use_thinking=True)
        except Exception as exc:  # noqa: BLE001 - recuperações pontuais; o resto sobe para o retry
            message = str(exc)
            lowered = message.lower()
            # Modelos sem "thinking" (ex.: lite) rejeitam o parâmetro com 400: repetir sem ele
            if self.thinking_budget is not None and ("thinking" in lowered or _is_invalid_argument(exc)):
                log.info("Modelo %s rejeitou o pedido (%s); a repetir sem thinking_config", self.model, message[:60])
                self.thinking_budget = None
                return self._raw_generate(system_instruction, prompt, use_thinking=False)
            # Modelo descontinuado: adotar o modelo sugerido pela própria API (uma única vez)
            suggested = self.suggested_model(message)
            if suggested and not self._model_switched and any(hint in lowered for hint in MODEL_UNAVAILABLE_HINTS):
                log.warning("Modelo %s indisponível na API; a usar o modelo sugerido: %s", self.model, suggested)
                self.model = suggested
                self._model_switched = True
                return self._raw_generate(system_instruction, prompt, use_thinking=self.thinking_budget is not None)
            raise

    def _call_with_retry(self, system_instruction: str, prompt: str) -> str:
        attempts = self.retries + 1
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.rate_limit_seconds:
                time.sleep(self.rate_limit_seconds - elapsed)
            try:
                self._last_call = time.monotonic()
                return self._generate_text(system_instruction, prompt)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= attempts or not _is_retryable(exc):
                    raise
                delay = self.backoff_base_seconds * (2 ** (attempt - 1))
                log.warning("Gemini: tentativa %d/%d falhou (%s); nova tentativa em %.0fs", attempt, attempts, str(exc)[:120], delay)
                time.sleep(delay)
        raise RuntimeError(str(last_exc))

    def _call_with_fallback(self, system_instruction: str, prompt: str) -> str:
        """Esgotadas as tentativas num modelo por erro transitório (ex.: 503), passa ao próximo modelo alternativo."""
        while True:
            try:
                return self._call_with_retry(system_instruction, prompt)
            except Exception as exc:  # noqa: BLE001
                if self.fallback_models and (_is_retryable(exc) or _is_quota_exhausted(exc)):
                    next_model = self.fallback_models.pop(0)
                    log.warning("Modelo %s indisponível (%s); a mudar para o modelo alternativo %s",
                                self.model, str(exc)[:80], next_model)
                    self.model = next_model
                    continue
                raise

    # ------------------------------------------------------------------ análise
    def analyze(
        self,
        company: dict[str, Any],
        explanation: str,
        news: list[NewsItem | dict[str, Any]],
        refresh: bool = False,
    ) -> AiResult:
        ticker = str(company.get("ticker"))
        today = date.today().isoformat()
        if self.cache is not None and not refresh:
            # procura pelo modelo atual e pelos alternativos: uma análise de hoje feita por um
            # modelo alternativo (ex.: principal sem quota) não é repetida
            for model in [self.model, *self.fallback_models]:
                payload = self.cache.get(f"ai:{model}:{ticker}:{today}", ttl_hours=self.cache_ttl_hours)
                if payload and payload.get("analysis"):
                    return AiResult.from_payload(payload)

        prompt = build_prompt(company, explanation, news)
        try:
            text = self._call_with_fallback(SYSTEM_INSTRUCTION, prompt)
            analysis = parse_analysis(text)
        except Exception as exc:  # noqa: BLE001 - a IA nunca pode parar o pipeline
            message = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.error("%s: análise com IA falhou (%s)", ticker, message)
            return AiResult(ticker=ticker, model=self.model, analysis=None, error=message)

        result = AiResult(ticker=ticker, model=self.model, analysis=analysis, review_required=bool(analysis["red_flags"]))
        if self.cache is not None:  # guardado sob o modelo efetivamente usado
            self.cache.set(f"ai:{self.model}:{ticker}:{date.today().isoformat()}", result.to_payload())
        return result

    def analyze_many(
        self,
        companies: list[tuple[dict[str, Any], str, list[NewsItem | dict[str, Any]]]],
        refresh: bool = False,
    ) -> dict[str, AiResult]:
        results: dict[str, AiResult] = {}
        for index, (company, explanation, news) in enumerate(companies, start=1):
            result = self.analyze(company, explanation, news, refresh=refresh)
            results[result.ticker] = result
            status = "cache" if result.from_cache else ("ok" if result.ok else "erro")
            log.info("IA %d/%d %s: %s%s", index, len(companies), result.ticker, status,
                     f" | {REVIEW_FLAG}" if result.review_required else "")
        return results
