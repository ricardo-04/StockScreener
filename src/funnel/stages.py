"""Etapas concretas do funil (1-6). Todos os limiares vêm do ``config.yaml``.

Convenções:
- cada etapa lê a configuração da sua secção (``stages.<nome>``); ``default`` +
  ``by_type.<tipo>`` são fundidos por tipo de negócio;
- dados em falta numa métrica crítica eliminam com a descrição
  "dados insuficientes: ..."; em métricas secundárias geram apenas aviso;
- a infraestrutura (``Stage``, ``RuleFailure``, ...) vive em ``src/funnel/base.py``
  e é re-exportada aqui por conveniência.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.funnel.base import (  # noqa: F401 - re-exportação
    PRE_STAGE_LABEL, PRE_STAGE_NAME, STAGE_REGISTRY, Elimination, RuleFailure, Stage, build_stages,
    check_max, check_min, format_value, register_stage, row_number, row_text,
)
from src.metrics.classification import BANK_INSURANCE, REIT, STANDARD
from src.metrics.leverage import DEBT_FREE, is_net_cash
from src.metrics.statements import NAN

__all__ = [
    "PRE_STAGE_LABEL", "PRE_STAGE_NAME", "STAGE_REGISTRY", "Elimination", "RuleFailure", "Stage",
    "build_stages", "check_max", "check_min", "format_value", "register_stage", "merged_type_config",
    "EligibilityStage", "FinancialHealthStage", "QualityStage", "GrowthStage", "ValuationStage",
    "MomentumStage",
]


def merged_type_config(stage_cfg: dict[str, Any], business_type: str, fallback: str = STANDARD) -> dict[str, Any]:
    """``default`` fundido com ``by_type[tipo]`` (ou ``by_type[fallback]`` se o tipo não existir)."""
    merged = dict(stage_cfg.get("default") or {})
    by_type = stage_cfg.get("by_type") or {}
    if business_type in by_type:
        merged.update(by_type[business_type] or {})
    elif fallback in by_type:
        merged.update(by_type[fallback] or {})
    return merged


def _isnan(value: float) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _business_type(row: pd.Series) -> str:
    return row_text(row, "business_type", STANDARD) or STANDARD


def _missing_data(rule: str, what: str, threshold: Any) -> RuleFailure:
    return RuleFailure(rule, f"dados insuficientes: {what}", NAN, threshold)


# ---------------------------------------------------------------------------
# Etapa 1 – Elegibilidade e dados
# ---------------------------------------------------------------------------
@register_stage
class EligibilityStage(Stage):
    name = "eligibility"
    order = 1
    label = "Etapa 1 – Elegibilidade e dados"

    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        cfg = self.config
        failures: list[RuleFailure] = []
        checks = (
            check_min(row_number(row, "market_cap"), float(cfg["min_market_cap_usd"]),
                      "min_market_cap", "Market cap mínimo (USD)"),
            check_min(row_number(row, "avg_dollar_volume_60d"), float(cfg["min_avg_dollar_volume_usd"]),
                      "min_avg_dollar_volume", "Volume médio diário mínimo em USD (60 dias)"),
            check_min(row_number(row, "price"), float(cfg["min_price_usd"]), "min_price", "Preço mínimo (USD)"),
            check_min(row_number(row, "years_of_statements"), float(cfg["min_statement_years"]),
                      "min_statement_years", "Anos de demonstrações financeiras"),
        )
        failures.extend(check for check in checks if check)

        business_type = _business_type(row)
        critical = cfg.get("critical_fields") or {}
        fields = critical.get(business_type) or critical.get(STANDARD) or []
        for field_name in fields:
            if _isnan(row_number(row, field_name)):
                failures.append(_missing_data("critical_fields", field_name, "campo disponível"))
        return failures

    def warnings(self, row: pd.Series) -> list[str]:
        expected = self.config.get("expected_currency")
        currency = row_text(row, "currency")
        if expected and currency and currency != expected:
            return [f"moeda de cotação {currency} ≠ {expected}: limiares em USD podem não se aplicar"]
        return []


# ---------------------------------------------------------------------------
# Etapa 2 – Saúde financeira (kill switches)
# ---------------------------------------------------------------------------
@register_stage
class FinancialHealthStage(Stage):
    name = "financial_health"
    order = 2
    label = "Etapa 2 – Saúde financeira"

    def _type_cfg(self, row: pd.Series) -> dict[str, Any]:
        return merged_type_config(self.config, _business_type(row))

    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        business_type = _business_type(row)
        type_cfg = self._type_cfg(row)
        cfg = self.config
        failures: list[RuleFailure] = []

        if business_type == BANK_INSURANCE:
            years = int(type_cfg.get("roe_positive_years", 3))
            roe_values = [row_number(row, f"roe_fy{i}") for i in range(years)]
            missing = sum(1 for v in roe_values if _isnan(v))
            if missing:
                failures.append(_missing_data("roe_history", f"ROE em {missing} dos últimos {years} anos", "ROE disponível"))
            elif any(v <= 0 for v in roe_values):
                failures.append(RuleFailure("roe_positive", f"ROE positivo em cada um dos últimos {years} anos",
                                            min(roe_values), 0.0))
            if type_cfg.get("require_price_to_book") and _isnan(row_number(row, "price_to_book")):
                failures.append(_missing_data("price_to_book_available", "P/B", "P/B disponível"))
            return failures

        # --- empresas não financeiras ---------------------------------------
        net_debt_value = row_number(row, "net_debt")
        nd_ratio = row_number(row, "net_debt_to_ebitda")
        max_nd = type_cfg.get("max_net_debt_to_ebitda")
        if max_nd is not None and not is_net_cash(net_debt_value):
            if _isnan(nd_ratio):
                failures.append(RuleFailure("max_net_debt_to_ebitda",
                                            "Net Debt/EBITDA máximo (EBITDA não positivo ou em falta)", NAN, float(max_nd)))
            elif nd_ratio > float(max_nd):
                failures.append(RuleFailure("max_net_debt_to_ebitda", "Net Debt/EBITDA máximo", nd_ratio, float(max_nd)))

        min_coverage = type_cfg.get("min_interest_coverage")
        if min_coverage is not None and row_text(row, "debt_status") != DEBT_FREE:
            coverage = row_number(row, "interest_coverage")
            if _isnan(coverage):
                if not self._coverage_immaterial(row):
                    failures.append(_missing_data("min_interest_coverage",
                                                  "despesa de juros (cobertura não calculável com dívida relevante)",
                                                  float(min_coverage)))
            elif coverage < float(min_coverage):
                failures.append(RuleFailure("min_interest_coverage", "Cobertura de juros mínima (EBIT/juros) ou sem dívida",
                                            coverage, float(min_coverage)))

        min_z = type_cfg.get("min_altman_z")
        if min_z is not None:
            check = check_min(row_number(row, "altman_z"), float(min_z), "min_altman_z", "Altman Z-Score mínimo", nan_fails=False)
            if check:
                failures.append(check)

        min_cr = type_cfg.get("min_current_ratio")
        if min_cr is not None:
            check = check_min(row_number(row, "current_ratio"), float(min_cr), "min_current_ratio", "Current ratio mínimo", nan_fails=False)
            if check:
                failures.append(check)

        min_f = cfg.get("min_piotroski")
        if min_f is not None and self._piotroski_usable(row):
            score = row_number(row, "piotroski_f")
            if score < float(min_f):
                failures.append(RuleFailure("min_piotroski", "Piotroski F-Score mínimo", score, float(min_f)))

        max_m = cfg.get("max_beneish_m")
        beneish = row_number(row, "beneish_m")
        if max_m is not None and not _isnan(beneish) and beneish > float(max_m):
            failures.append(RuleFailure("max_beneish_m", "Beneish M-Score máximo (indício de manipulação contabilística)",
                                        beneish, float(max_m)))
        return failures

    def _coverage_immaterial(self, row: pd.Series) -> bool:
        """Dívida imaterial (caixa líquida ou ND/EBITDA baixo) sem juros reportados: não elimina."""
        if is_net_cash(row_number(row, "net_debt")):
            return True
        limit = float(self.config.get("interest_coverage_ignore_if_nd_ebitda_below", 1.0))
        nd_ratio = row_number(row, "net_debt_to_ebitda")
        return not _isnan(nd_ratio) and nd_ratio <= limit

    def _piotroski_usable(self, row: pd.Series) -> bool:
        score = row_number(row, "piotroski_f")
        criteria = row_number(row, "piotroski_criteria")
        min_criteria = float(self.config.get("piotroski_min_criteria", 7))
        return not _isnan(score) and not _isnan(criteria) and criteria >= min_criteria

    def warnings(self, row: pd.Series) -> list[str]:
        if _business_type(row) == BANK_INSURANCE:
            return []
        type_cfg = self._type_cfg(row)
        notes: list[str] = []
        if type_cfg.get("min_interest_coverage") is not None and row_text(row, "debt_status") != DEBT_FREE:
            if _isnan(row_number(row, "interest_coverage")) and self._coverage_immaterial(row):
                notes.append("cobertura de juros não calculável (sem juros reportados), dívida imaterial")
            elif "último ano em falta" in row_text(row, "interest_expense_source"):
                notes.append("cobertura de juros calculada com a despesa de juros do ano anterior (último ano em falta)")
        if type_cfg.get("min_altman_z") is not None and _isnan(row_number(row, "altman_z")):
            notes.append("Altman Z não calculável")
        if type_cfg.get("min_current_ratio") is not None and _isnan(row_number(row, "current_ratio")):
            notes.append("current ratio não disponível")
        if self.config.get("min_piotroski") is not None and not self._piotroski_usable(row):
            criteria = row_number(row, "piotroski_criteria")
            notes.append(f"Piotroski incompleto ({0 if _isnan(criteria) else int(criteria)}/9 critérios avaliáveis)")
        if self.config.get("max_beneish_m") is not None and _isnan(row_number(row, "beneish_m")):
            notes.append("Beneish M não calculável")
        return notes


# ---------------------------------------------------------------------------
# Etapa 3 – Qualidade do negócio
# ---------------------------------------------------------------------------
@register_stage
class QualityStage(Stage):
    name = "quality"
    order = 3
    label = "Etapa 3 – Qualidade do negócio"

    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        business_type = _business_type(row)
        cfg = merged_type_config(self.config, business_type)
        failures: list[RuleFailure] = []

        if business_type == BANK_INSURANCE:
            min_roe = cfg.get("min_roe_avg_3y")
            if min_roe is not None:
                roe_avg = row_number(row, "roe_avg_3y")
                if _isnan(roe_avg):
                    failures.append(_missing_data("min_roe_avg_3y", "ROE (capital próprio ou lucro em falta)", float(min_roe)))
                elif roe_avg < float(min_roe):
                    failures.append(RuleFailure("min_roe_avg_3y", "ROE médio 3 anos mínimo", roe_avg, float(min_roe)))
        else:
            min_roic = cfg.get("min_roic_avg_3y")
            if min_roic is not None:
                roic_avg = row_number(row, "roic_avg_3y")
                observed = row_number(row, "roic_years_observed")
                if _isnan(roic_avg) or _isnan(observed) or observed < float(cfg.get("min_roic_years", 2)):
                    failures.append(_missing_data("min_roic_avg_3y", "ROIC (EBIT ou capital investido em falta)", float(min_roic)))
                elif roic_avg < float(min_roic):
                    failures.append(RuleFailure("min_roic_avg_3y", "ROIC médio 3 anos mínimo", roic_avg, float(min_roic)))

            need = cfg.get("min_fcf_positive_years")
            if need is not None:
                window = int(cfg.get("fcf_window_years", 4))
                positive = row_number(row, "fcf_positive_years")
                observed = row_number(row, "fcf_years_observed")
                if _isnan(observed) or observed < float(need):
                    failures.append(_missing_data("min_fcf_positive_years",
                                                  f"FCF disponível em apenas {0 if _isnan(observed) else int(observed)} dos últimos {window} anos",
                                                  float(need)))
                elif positive < float(need):
                    failures.append(RuleFailure("min_fcf_positive_years",
                                                f"FCF positivo em pelo menos {int(need)} dos últimos {window} anos",
                                                positive, float(need)))

            min_cc = cfg.get("min_cash_conversion")
            if min_cc is not None:
                conversion = row_number(row, "cash_conversion")
                if _isnan(conversion):
                    failures.append(RuleFailure("min_cash_conversion",
                                                "Conversão de caixa FCF/Lucro líquido (lucro acumulado não positivo ou dados em falta)",
                                                NAN, float(min_cc)))
                elif conversion < float(min_cc):
                    failures.append(RuleFailure("min_cash_conversion", "Conversão de caixa FCF/Lucro líquido mínima",
                                                conversion, float(min_cc)))

            max_std = cfg.get("max_gross_margin_std_pp")
            if max_std is not None and cfg.get("gross_margin_eliminates", True):
                std = row_number(row, "gross_margin_std_pp")
                if not _isnan(std) and std > float(max_std):
                    failures.append(RuleFailure("max_gross_margin_std", "Estabilidade da margem bruta (desvio padrão em p.p.)",
                                                std, float(max_std)))

        max_dilution = cfg.get("max_share_dilution_cagr")
        if max_dilution is not None:
            dilution = row_number(row, "share_dilution_cagr")
            if not _isnan(dilution) and dilution > float(max_dilution):
                failures.append(RuleFailure("max_share_dilution", "Diluição: crescimento anual do nº de ações",
                                            dilution, float(max_dilution)))
        return failures

    def warnings(self, row: pd.Series) -> list[str]:
        business_type = _business_type(row)
        cfg = merged_type_config(self.config, business_type)
        notes: list[str] = []
        if business_type != BANK_INSURANCE:
            std = row_number(row, "gross_margin_std_pp")
            max_std = cfg.get("max_gross_margin_std_pp")
            if _isnan(std):
                notes.append("estabilidade da margem bruta não calculável")
            elif max_std is not None and not cfg.get("gross_margin_eliminates", True) and std > float(max_std):
                notes.append(f"margem bruta volátil (desvio padrão {std:.1f} p.p.), tolerado para {business_type}")
        if _isnan(row_number(row, "share_dilution_cagr")):
            notes.append("diluição não calculável (nº de ações em falta)")
        return notes


# ---------------------------------------------------------------------------
# Etapa 4 – Crescimento
# ---------------------------------------------------------------------------
@register_stage
class GrowthStage(Stage):
    name = "growth"
    order = 4
    label = "Etapa 4 – Crescimento"

    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        cfg = merged_type_config(self.config, _business_type(row))
        failures: list[RuleFailure] = []

        min_cagr = cfg.get("min_revenue_cagr_3y")
        if min_cagr is not None:
            growth = row_number(row, "revenue_cagr_3y")
            if _isnan(growth):
                failures.append(_missing_data("min_revenue_cagr_3y", "receita histórica (CAGR não calculável)", float(min_cagr)))
            elif growth < float(min_cagr):
                failures.append(RuleFailure("min_revenue_cagr_3y", "CAGR da receita (3 anos) mínimo", growth, float(min_cagr)))

        max_declines = cfg.get("max_consecutive_eps_declines")
        if max_declines is not None:
            declines = row_number(row, "eps_consecutive_declines")
            if not _isnan(declines) and declines > float(max_declines):
                failures.append(RuleFailure("max_eps_declines", "EPS em queda em anos consecutivos", declines, float(max_declines)))

        if cfg.get("require_nonnegative_expected_revenue_growth"):
            expected = row_number(row, "revenue_growth_est_next_fy")
            if not _isnan(expected) and expected < 0:
                failures.append(RuleFailure("expected_revenue_growth", "Crescimento de receita esperado não negativo",
                                            expected, 0.0))
        return failures

    def warnings(self, row: pd.Series) -> list[str]:
        notes: list[str] = []
        span = row_number(row, "revenue_cagr_span_years")
        target = int(self.global_config.get("metrics", {}).get("revenue_cagr_years", 3))
        if not _isnan(span) and 0 < span < target:
            notes.append(f"CAGR da receita calculado sobre {int(span)} anos (histórico curto)")
        if _isnan(row_number(row, "eps_consecutive_declines")):
            notes.append("EPS histórico insuficiente para avaliar quedas consecutivas")
        if _isnan(row_number(row, "revenue_growth_est_next_fy")):
            notes.append("sem estimativa de crescimento de receita")
        return notes


# ---------------------------------------------------------------------------
# Etapa 5 – Valuation relativa ao setor
# ---------------------------------------------------------------------------
@register_stage
class ValuationStage(Stage):
    name = "valuation"
    order = 5
    label = "Etapa 5 – Valuation"

    def _small_sector(self, row: pd.Series) -> bool:
        size = row_number(row, "sector_size")
        return _isnan(size) or size < float(self.config.get("min_sector_size", 5))

    def _relative_failures(self, row: pd.Series, cfg: dict[str, Any], business_type: str) -> list[RuleFailure]:
        """Regras que dependem de percentis do setor (ignoradas, com aviso, em setores pequenos)."""
        failures: list[RuleFailure] = []
        if business_type == BANK_INSURANCE:
            max_pct = cfg.get("max_pb_sector_pct")
            if max_pct is not None:
                pct = row_number(row, "price_to_book_sector_pct")
                if _isnan(pct):
                    failures.append(_missing_data("max_pb_sector_pct", "P/B", float(max_pct)))
                elif pct > float(max_pct):
                    failures.append(RuleFailure("max_pb_sector_pct", "P/B relativo ao setor (percentil máximo)", pct, float(max_pct)))
            return failures
        if business_type == REIT:
            return failures
        min_pct = cfg.get("min_fcf_yield_sector_pct")
        if min_pct is not None:
            pct = row_number(row, "fcf_yield_sector_pct")
            if _isnan(pct):
                failures.append(_missing_data("min_fcf_yield_sector_pct", "FCF yield (FCF ou EV em falta)", float(min_pct)))
            elif pct < float(min_pct):
                failures.append(RuleFailure("min_fcf_yield_sector_pct", "FCF yield relativo ao setor (percentil mínimo)",
                                            pct, float(min_pct)))
        max_pct = cfg.get("max_ev_ebit_sector_pct")
        if max_pct is not None:
            pct = row_number(row, "ev_to_ebit_sector_pct")
            if _isnan(pct):
                failures.append(RuleFailure("max_ev_ebit_sector_pct", "EV/EBIT não calculável (EBIT não positivo ou EV em falta)",
                                            NAN, float(max_pct)))
            elif pct > float(max_pct):
                failures.append(RuleFailure("max_ev_ebit_sector_pct", "EV/EBIT relativo ao setor (percentil máximo)",
                                            pct, float(max_pct)))
        return failures

    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        business_type = _business_type(row)
        cfg = merged_type_config(self.config, business_type)
        failures: list[RuleFailure] = []
        if not self._small_sector(row):
            failures.extend(self._relative_failures(row, cfg, business_type))

        forward_pe = row_number(row, "forward_pe")
        if business_type == BANK_INSURANCE:
            max_pe = cfg.get("max_forward_pe")
            if max_pe is not None and not _isnan(forward_pe) and (forward_pe <= 0 or forward_pe > float(max_pe)):
                failures.append(RuleFailure("max_forward_pe", f"Forward P/E entre 0 e {float(max_pe):g} (financeiras)",
                                            forward_pe, float(max_pe)))
        elif business_type == REIT:
            min_dy = cfg.get("min_dividend_yield")
            dividend_yield = row_number(row, "dividend_yield")
            if min_dy is not None and not _isnan(dividend_yield) and dividend_yield < float(min_dy):
                failures.append(RuleFailure("min_dividend_yield", "Dividend yield mínimo (REIT)", dividend_yield, float(min_dy)))
            max_pffo = cfg.get("max_p_ffo")
            p_ffo = row_number(row, "p_ffo")
            if max_pffo is not None and not _isnan(p_ffo) and p_ffo > float(max_pffo):
                failures.append(RuleFailure("max_p_ffo", "P/FFO aproximado máximo (REIT)", p_ffo, float(max_pffo)))
        else:
            low, high = cfg.get("forward_pe_min"), cfg.get("forward_pe_max")
            if high is not None and not _isnan(forward_pe) and (forward_pe <= float(low or 0.0) or forward_pe > float(high)):
                failures.append(RuleFailure("forward_pe_range", f"Forward P/E entre {float(low or 0.0):g} e {float(high):g}",
                                            forward_pe, float(high)))
        return failures

    def warnings(self, row: pd.Series) -> list[str]:
        business_type = _business_type(row)
        cfg = merged_type_config(self.config, business_type)
        notes: list[str] = []
        if self._small_sector(row):
            size = row_number(row, "sector_size")
            size_text = "n/d" if _isnan(size) else str(int(size))
            notes.append(f"setor com {size_text} empresas no universo: regras relativas ao setor só como aviso")
            for failure in self._relative_failures(row, cfg, business_type):
                notes.append(f"(aviso) {failure.text()}")
        max_peg = self.config.get("max_peg_warning")
        peg = row_number(row, "peg_ratio")
        if max_peg is not None and not _isnan(peg) and peg > float(max_peg):
            notes.append(f"PEG de {peg:.1f} acima de {float(max_peg):g}")
        if _isnan(row_number(row, "forward_pe")):
            notes.append("Forward P/E não disponível")
        if business_type == REIT:
            if _isnan(row_number(row, "dividend_yield")):
                notes.append("dividend yield não disponível (REIT)")
            if _isnan(row_number(row, "p_ffo")):
                notes.append("P/FFO não calculável (REIT)")
        return notes


# ---------------------------------------------------------------------------
# Etapa 6 – Momentum / timing
# ---------------------------------------------------------------------------
@register_stage
class MomentumStage(Stage):
    name = "momentum"
    order = 6
    label = "Etapa 6 – Momentum"

    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        minimum = self.config.get("min_momentum_12_1")
        momentum = row_number(row, "momentum_12_1")
        if minimum is not None and not _isnan(momentum) and momentum <= float(minimum):
            return [RuleFailure("min_momentum_12_1", "Momentum 12-1 meses mínimo", momentum, float(minimum))]
        return []

    def warnings(self, row: pd.Series) -> list[str]:
        momentum = row_number(row, "momentum_12_1")
        if _isnan(momentum):
            return ["momentum 12-1 não calculável (histórico de preços < 13 meses)"]
        notes: list[str] = []
        knife = self.config.get("falling_knife_momentum")
        price, sma = row_number(row, "price"), row_number(row, "sma_200")
        if knife is not None and not _isnan(price) and not _isnan(sma) and price < sma and momentum < float(knife):
            notes.append(f"faca a cair: preço abaixo da SMA200 e momentum 12-1 de {momentum:.1%}")
        return notes
