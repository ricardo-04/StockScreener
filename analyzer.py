import numpy as np
import pandas as pd
from typing import Tuple

def calculate_advanced_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula as métricas quantitativas avançadas de Qualidade, Solvência, Valuation e Momentum.
    Trata separadamente instituições financeiras, excluindo-as dos cálculos industriais de FCF e Dívida Líquida.
    """
    df = df.copy()

    # 1. Qualidade
    # ROIC = NOPAT / Invested Capital (aproximado por EBIT * (1 - t) / Invested Capital com t=21%)
    tax_rate = 0.21
    def calc_roic(row):
        if row.get("IsFinancial", False):
            return np.nan
        ebit = row.get("EBIT")
        ic = row.get("InvestedCapital")
        if pd.notnull(ebit) and pd.notnull(ic) and ic > 0:
            roic = (ebit * (1 - tax_rate)) / ic
            return min(max(roic, -0.5), 0.80)  # Teto em 80% (0.80) e piso em -50%
        op_margin = row.get("OperatingMargin")
        if pd.notnull(op_margin):
            return min(max(op_margin * 0.75, -0.2), 0.80)
        return np.nan

    df["ROIC"] = df.apply(calc_roic, axis=1)

    # 2. Solvência (excluindo estritamente Financial Services)
    def calc_net_debt_ebitda(row):
        if row.get("IsFinancial", False):
            return np.nan
        debt = row.get("TotalDebt", 0.0) or 0.0
        cash = row.get("TotalCash", 0.0) or 0.0
        net_debt = debt - cash
        ebitda = row.get("EBITDA")
        if pd.notnull(ebitda) and ebitda > 0:
            return net_debt / ebitda
        return np.nan

    df["NetDebt_EBITDA"] = df.apply(calc_net_debt_ebitda, axis=1)

    def calc_interest_coverage(row):
        if row.get("IsFinancial", False):
            return np.nan
        ebit = row.get("EBIT")
        ie = row.get("InterestExpense")
        if pd.notnull(ebit) and pd.notnull(ie) and ie > 0:
            return max(ebit / ie, -10.0)
        elif pd.notnull(ebit) and (ie is None or ie == 0):
            return 25.0
        return np.nan

    df["InterestCoverage"] = df.apply(calc_interest_coverage, axis=1)

    # 3. Valuation (exclui FCF industrial de financeiras)
    def calc_fcf_yield(row):
        if row.get("IsFinancial", False):
            return np.nan
        fcf = row.get("FCF")
        mcap = row.get("MarketCap")
        if pd.notnull(fcf) and pd.notnull(mcap) and mcap > 0:
            return fcf / mcap
        return np.nan

    df["FCFYield"] = df.apply(calc_fcf_yield, axis=1)

    def calc_discount_52w(row):
        p = row.get("CurrentPrice")
        high = row.get("52WeekHigh")
        if pd.notnull(p) and pd.notnull(high) and high > 0:
            return (high - p) / high
        return 0.0

    df["Discount_52W_High"] = df.apply(calc_discount_52w, axis=1)

    def calc_analyst_upside(row):
        p = row.get("CurrentPrice")
        target = row.get("TargetMeanPrice")
        if pd.notnull(p) and pd.notnull(target) and p > 0:
            return (target - p) / p
        return 0.0

    df["Analyst_Upside"] = df.apply(calc_analyst_upside, axis=1)

    # 4. Momentum Técnico
    def calc_price_vs_sma200(row):
        p = row.get("CurrentPrice")
        sma = row.get("SMA200")
        if pd.notnull(p) and pd.notnull(sma) and sma > 0:
            return (p - sma) / sma
        return 0.0

    df["Price_vs_SMA200"] = df.apply(calc_price_vs_sma200, axis=1)

    return df

def apply_sector_normalization(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula percentis relativos (0 a 100) para ROIC, Margem Operacional, FCF Yield e ROE
    DENTRO de cada setor via groupby.
    """
    df = df.copy()

    for col in ["ROIC", "OperatingMargin", "FCFYield", "ROE"]:
        if col in df.columns:
            # Groupby setor e calcular rank percentual (0 a 100)
            df[f"{col}_Sector_Pct"] = df.groupby("Sector")[col].rank(pct=True, ascending=True) * 100
            # Preencher nulos com mediana (50)
            df[f"{col}_Sector_Pct"] = df[f"{col}_Sector_Pct"].fillna(50.0)

    return df

def classify_and_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Classifica empresas em arquétipos institucionais:
    - 'Compounder_GARP': Restrito a setores não-commodities, com alto ROIC sustentável.
    - 'Ciclicas_Commodities': Exclusivo para setores 'Energy' e 'Basic Materials' / 'Materials'.
    - 'Deep_Value_Turnaround': Desconto acentuado com fluxo de caixa livre sólido.
    - 'Financeiras_Bancos': Instituições financeiras avaliadas por ROE e Forward P/E.
    """
    df = df.copy()

    archetypes = []
    scores = []
    verdicts = []

    for _, row in df.iterrows():
        sector = str(row.get("Sector", "")).strip()
        is_fin = bool(row.get("IsFinancial", False)) or ("financial" in sector.lower())
        
        # Identificação de Commodities / Cíclicas
        is_commodity = any(comm in sector.lower() for comm in ["energy", "basic materials", "materials"])

        roic = row.get("ROIC", 0.0) or 0.0
        roic_pct = row.get("ROIC_Sector_Pct", 50.0) or 50.0
        op_margin_pct = row.get("OperatingMargin_Sector_Pct", 50.0) or 50.0
        fcf_yield = row.get("FCFYield", 0.0) or 0.0
        fcf_pct = row.get("FCFYield_Sector_Pct", 50.0) or 50.0
        roe = row.get("ROE", 0.0) or 0.0
        roe_pct = row.get("ROE_Sector_Pct", 50.0) or 50.0
        
        fwd_pe = row.get("ForwardPE")
        peg = row.get("PEGRatio")
        disc_52w = row.get("Discount_52W_High", 0.0) or 0.0
        upside = row.get("Analyst_Upside", 0.0) or 0.0
        interest_cov = row.get("InterestCoverage")
        net_debt_ebitda = row.get("NetDebt_EBITDA")
        sma_diff = row.get("Price_vs_SMA200", 0.0) or 0.0

        # === DETERMINAÇÃO DO ARQUÉTIPO ===
        if is_fin:
            archetype = "Financeiras_Bancos"
        elif is_commodity:
            archetype = "Ciclicas_Commodities"
        else:
            # Compounder_GARP: Apenas não-commodities, alto ROIC/Margens, avaliação justa
            is_compounder = (
                (roic_pct >= 60.0 or roic >= 0.15) and
                op_margin_pct >= 55.0 and
                (fwd_pe is None or fwd_pe <= 38.0) and
                (peg is None or peg <= 2.8) and
                (interest_cov is None or interest_cov >= 3.5)
            )

            is_deep_value = (
                disc_52w >= 0.15 and
                fcf_yield >= 0.055 and
                (interest_cov is None or interest_cov >= 4.0) and
                (net_debt_ebitda is None or net_debt_ebitda <= 3.5)
            )

            if is_compounder and is_deep_value:
                archetype = "Compounder_GARP & Deep_Value"
            elif is_compounder:
                archetype = "Compounder_GARP"
            elif is_deep_value:
                archetype = "Deep_Value_Turnaround"
            else:
                archetype = "Neutro / Misto"

        # === CÁLCULO DO SCORE INSTITUCIONAL (0-100) ===
        if is_fin:
            # Avaliação especializada de Bancos/Financeiras (ROE + Forward P/E + Upside + SMA)
            # 1. Rentabilidade do Capital Próprio (ROE) - 40 pts
            q_score = (roe_pct * 0.25)
            if roe >= 0.18:
                q_score += 15.0
            elif roe >= 0.12:
                q_score += 10.0
            elif roe >= 0.08:
                q_score += 5.0

            # 2. Valuation (Forward P/E) - 35 pts
            val_score = 15.0
            if fwd_pe is not None and fwd_pe > 0:
                if fwd_pe <= 10.0:
                    val_score += 20.0
                elif fwd_pe <= 15.0:
                    val_score += 15.0
                elif fwd_pe <= 22.0:
                    val_score += 8.0
                elif fwd_pe > 30.0:
                    val_score -= 5.0

            # 3. Momentum & Preço-Alvo - 25 pts
            mom_score = 10.0
            if upside > 0.15:
                mom_score += 8.0
            elif upside > 0.05:
                mom_score += 4.0
            if sma_diff > 0:
                mom_score += 7.0

            total_score = min(max(round(q_score + val_score + mom_score, 1), 0.0), 100.0)

            # Veredito Financeiras
            if total_score >= 80.0:
                verdict = "OPORTUNIDADE DE VALOR (FINANCEIRO)"
            elif total_score >= 70.0:
                verdict = "COMPRA MODERADA"
            elif (fwd_pe is not None and fwd_pe > 25.0) or total_score < 55.0:
                verdict = "AGUARDAR / CARO"
            else:
                verdict = "AGUARDAR / CARO" if total_score < 65.0 else "COMPRA MODERADA"

        elif is_commodity:
            # Avaliação de Cíclicas/Commodities: Foco em Valuation barato e desconfiança de picos cíclicos
            q_score = (roic_pct * 0.10) + (op_margin_pct * 0.10)
            val_score = (fcf_pct * 0.15)
            if fwd_pe is not None and 0 < fwd_pe <= 15:
                val_score += 8.0
            elif fwd_pe is not None and 15 < fwd_pe <= 25:
                val_score += 4.0
                
            solv_score = 10.0
            if interest_cov is not None:
                if interest_cov >= 5.0:
                    solv_score += 5.0
                elif interest_cov < 2.0:
                    solv_score -= 8.0
            if net_debt_ebitda is not None and net_debt_ebitda <= 2.0:
                solv_score += 5.0

            mom_score = 5.0
            if upside > 0.15:
                mom_score += 5.0
            if sma_diff > 0:
                mom_score += 5.0

            total_score = min(max(round(q_score + val_score + solv_score + mom_score, 1), 0.0), 88.0) # Teto prudencial cíclico

            has_credit_risk = (
                (interest_cov is not None and interest_cov < 1.5) or
                (net_debt_ebitda is not None and net_debt_ebitda > 4.5)
            )

            if has_credit_risk:
                verdict = "REJEITAR (RISCO DE CRÉDITO/OPERACIONAL)"
            elif disc_52w >= 0.15 and fcf_yield >= 0.06 and total_score >= 72.0:
                verdict = "OPORTUNIDADE DE VALOR (TURNAROUND)"
            elif total_score >= 68.0:
                verdict = "COMPRA MODERADA"
            else:
                verdict = "AGUARDAR / CARO"

        else:
            # Corporações Industriais, Tecnologia, Consumo, Saúde, etc.
            q_score = (roic_pct * 0.18) + (op_margin_pct * 0.12)
            val_score = (fcf_pct * 0.15)
            if fwd_pe is not None:
                if 0 < fwd_pe <= 18:
                    val_score += 10.0
                elif 18 < fwd_pe <= 28:
                    val_score += 7.0
                elif 28 < fwd_pe <= 40:
                    val_score += 3.0
            else:
                val_score += 5.0
                
            if peg is not None and 0 < peg <= 1.8:
                val_score += 5.0
            else:
                val_score += 2.5

            solv_score = 10.0
            if interest_cov is not None:
                if interest_cov >= 6.0:
                    solv_score += 6.0
                elif interest_cov >= 3.0:
                    solv_score += 3.0
                elif interest_cov < 1.5:
                    solv_score -= 8.0
            if net_debt_ebitda is not None:
                if net_debt_ebitda <= 2.0:
                    solv_score += 4.0
                elif net_debt_ebitda > 4.5:
                    solv_score -= 6.0

            mom_score = 5.0
            if upside > 0.15:
                mom_score += 8.0
            elif upside > 0.05:
                mom_score += 5.0

            if sma_diff > 0:
                mom_score += 7.0
            elif disc_52w >= 0.20 and fcf_yield >= 0.06:
                mom_score += 5.0

            total_score = min(max(round(q_score + val_score + solv_score + mom_score, 1), 0.0), 100.0)

            has_credit_risk = (
                (interest_cov is not None and interest_cov < 1.5) or
                (net_debt_ebitda is not None and net_debt_ebitda > 5.5)
            )

            if has_credit_risk:
                verdict = "REJEITAR (RISCO DE CRÉDITO/OPERACIONAL)"
            elif "Compounder" in archetype and total_score >= 78.0:
                verdict = "FORTE CONVICÇÃO (COMPOUNDER)"
            elif "Deep_Value" in archetype and total_score >= 74.0:
                verdict = "OPORTUNIDADE DE VALOR (TURNAROUND)"
            elif total_score >= 70.0:
                verdict = "COMPRA MODERADA"
            elif (fwd_pe is not None and fwd_pe > 45.0) or (total_score < 55.0 and disc_52w < 0.08):
                verdict = "AGUARDAR / CARO"
            else:
                verdict = "AGUARDAR / CARO" if total_score < 65.0 else "COMPRA MODERADA"

        archetypes.append(archetype)
        scores.append(total_score)
        verdicts.append(verdict)

    df["Archetype"] = archetypes
    df["Composite_Score"] = scores
    df["Verdict"] = verdicts

    return df
