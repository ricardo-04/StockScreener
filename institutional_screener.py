import os
import warnings
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from tabulate import tabulate

warnings.filterwarnings("ignore")


class InstitutionalScreenerFinal:

  def __init__(self, tickers: list[str]):
    self.tickers = tickers

  def _safe_get(
      self, df: pd.DataFrame, row_names: list[str], default=0.0
  ) -> float:
    for name in row_names:
      if name in df.index:
        val = df.loc[name].iloc[0]
        if pd.notna(val) and val != 0:
          return float(val)
    return float(default)

  def analyze_ticker(self, ticker: str) -> dict | None:
    try:
      stock = yf.Ticker(ticker)
      info = stock.info

      sector = info.get("sector", "N/A")
      if sector == "Financial Services":
        return None

      market_cap = info.get("marketCap", 0)
      if not market_cap or market_cap < 3_000_000_000:
        return None

      current_price = info.get(
          "currentPrice", info.get("regularMarketPrice", 0.0)
      )
      high_52 = info.get("fiftyTwoWeekHigh", 0.0)
      if current_price <= 0 or high_52 <= 0:
        return None

      discount_from_high = ((current_price - high_52) / high_52) * 100

      inc = stock.financials
      bs = stock.balance_sheet
      cf = stock.cashflow
      if inc.empty or bs.empty or cf.empty:
        return None

      ebit = self._safe_get(
          inc, ["EBIT", "Operating Income", "Operating Profit"]
      )
      net_income = self._safe_get(
          inc, ["Net Income", "Net Income Common Stockholders"]
      )
      tax_rate = min(
          max(self._safe_get(inc, ["Tax Rate For Calcs"], default=0.21), 0.15),
          0.30,
      )

      total_debt = self._safe_get(bs, ["Total Debt", "Long Term Debt"])
      cash = self._safe_get(
          bs,
          [
              "Cash And Cash Equivalents",
              "Cash Cash Equivalents And Short Term Investments",
          ],
      )
      equity = self._safe_get(
          bs, ["Stockholders Equity", "Total Equity Gross Minority Interest"]
      )

      invested_capital = total_debt + equity - cash
      if invested_capital < (market_cap * 0.10):
        invested_capital = max(equity, market_cap * 0.20)

      operating_cf = self._safe_get(
          cf,
          [
              "Operating Cash Flow",
              "Cash Flow From Continuing Operating Activities",
          ],
      )
      capex = abs(
          self._safe_get(cf, ["Capital Expenditure", "Capital Expenditures"])
      )
      fcf = (
          operating_cf - capex
          if capex > 0
          else self._safe_get(
              cf, ["Free Cash Flow"], default=operating_cf * 0.75
          )
      )

      nopat = ebit * (1 - tax_rate)
      raw_roic = (
          (nopat / invested_capital) * 100 if invested_capital > 0 else 0.0
      )
      roic = min(raw_roic, 80.0)

      cash_conversion = (fcf / net_income) if net_income > 0 else 0.0
      cash_conversion = min(cash_conversion, 2.5)

      ebitda = self._safe_get(
          inc, ["EBITDA", "Normalized EBITDA"], default=ebit * 1.15
      )
      net_debt = total_debt - cash
      net_debt_to_ebitda = (net_debt / ebitda) if ebitda > 0 else 999.0

      enterprise_value = info.get("enterpriseValue", market_cap + net_debt)
      fcf_yield = (
          (fcf / enterprise_value) * 100 if enterprise_value > 0 else 0.0
      )
      forward_pe = info.get("forwardPE", info.get("trailingPE", 0))

      op_margin = (
          info.get("operatingMargins", 0.0) * 100
          if info.get("operatingMargins")
          else (ebit / info.get("totalRevenue", 1)) * 100
      )
      rev_growth = (
          info.get("revenueGrowth", 0.0) * 100
          if info.get("revenueGrowth")
          else 0.0
      )

      # Correção da escala do dividendo
      raw_div = info.get("dividendYield", 0.0) or 0.0
      div_yield = raw_div if raw_div <= 0.25 else raw_div / 100.0
      div_yield = div_yield * 100 if div_yield <= 0.25 else div_yield
      div_yield = min(div_yield, 20.0)

      peg_ratio = info.get("pegRatio", 0.0) or 0.0

      # Estimativa de Potencial de Subida (Upside) via convergência de FCF Yield
      target_yield = 4.5  # Rendimento médio justo de FCF para empresas com moat
      fair_value = current_price * (fcf_yield / target_yield)
      upside = max(
          ((fair_value - current_price) / current_price) * 100, -30.0
      ) if fcf_yield > 0 else 0.0

      if roic < 11.0 or cash_conversion < 0.70 or fcf <= 0:
        return None
      if net_debt_to_ebitda > 3.0 or forward_pe <= 0 or forward_pe > 45:
        return None

      # Pontuação
      score_quality = (min(roic / 30.0, 1.0) * 20) + (
          min(max(op_margin, 0) / 25.0, 1.0) * 15
      )
      score_balance = (min(cash_conversion, 1.3) / 1.3 * 15) + (
          max(0, 3.0 - max(0, net_debt_to_ebitda)) / 3.0 * 15
      )

      valuation_points = min(max(fcf_yield, 0) / 6.0, 1.0) * 20
      discount_bonus = (
          15
          if -45.0 <= discount_from_high <= -18.0
          else (8 if discount_from_high < -10.0 else 0)
      )
      penalization_rev = -8 if rev_growth < 0 else 0

      total_score = round(
          score_quality
          + score_balance
          + valuation_points
          + discount_bonus
          + penalization_rev,
          1,
      )

      # Veredito Explícito de Decisão
      if total_score >= 88 and discount_from_high <= -20.0 and rev_growth >= 0:
        veredito = "FORTE OPORTUNIDADE DE COMPRA"
      elif total_score >= 76 and discount_from_high <= -12.0:
        veredito = "COMPRA MODERADA / ACUMULAÇÃO"
      elif discount_from_high > -10.0:
        veredito = "AGUARDAR (PREÇO ESTICADO / SEM DESCONTO)"
      elif rev_growth < -5.0:
        veredito = "NÃO RECOMENDADA (CONTRAÇÃO OPERACIONAL)"
      else:
        veredito = "NEUTRO / MANTER EM WATCHLIST"

      return {
          "Ticker": ticker,
          "Nome": info.get("shortName", ticker)[:20],
          "Setor": sector,
          "Preço ($)": round(current_price, 2),
          "Queda 52S (%)": round(discount_from_high, 1),
          "Upside Est. (%)": (
              f"+{round(upside, 1)}%"
              if upside > 0
              else f"{round(upside, 1)}%"
          ),
          "ROIC (%)": round(roic, 1),
          "Margem Op. (%)": round(op_margin, 1),
          "Cresc. Receita (%)": round(rev_growth, 1),
          "FCF Yield (%)": round(fcf_yield, 1),
          "P/E": round(forward_pe, 1),
          "Div. Yield (%)": f"{round(div_yield, 2)}%",
          "Dívida Líq/EBITDA": (
              "Caixa Líq."
              if net_debt <= 0
              else f"{round(net_debt_to_ebitda, 1)}x"
          ),
          "Score": total_score,
          "Veredito de Investimento": veredito,
      }
    except Exception:
      return None

  def run(self) -> tuple[pd.DataFrame, pd.DataFrame]:
    print(
        f"A analisar {len(self.tickers)} empresas através dos critérios"
        " finais..."
    )
    results = []
    for t in self.tickers:
      data = self.analyze_ticker(t)
      if data:
        results.append(data)

    if not results:
      return pd.DataFrame(), pd.DataFrame()

    full_df = (
        pd.DataFrame(results).sort_values(by="Score", ascending=False).copy()
    )
    top_df = (
        full_df[
            full_df["Veredito de Investimento"].str.startswith(
                ("FORTE", "COMPRA")
            )
        ]
        .head(20)
        .reset_index(drop=True)
        .copy()
    )
    return top_df, full_df.reset_index(drop=True)


if __name__ == "__main__":
  response = requests.get(
      "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
      headers={"User-Agent": "InstitutionalScreener/3.0"},
      timeout=30,
  )
  sp500_table = pd.read_html(StringIO(response.text))[0]
  watchlist = sp500_table["Symbol"].str.replace(".", "-", regex=False).tolist()

  screener = InstitutionalScreenerFinal(watchlist)
  top_df, full_df = screener.run()

  if not top_df.empty:
    output_path = Path(__file__).resolve().with_name(
        "ranking_institucional_definitivo.xlsx"
    )
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
      top_df.to_excel(
          writer, sheet_name="Top_Compras_Recomendadas", index=False
      )
      full_df.to_excel(writer, sheet_name="Todas_Analisadas", index=False)

    print(f"\nExcel guardado com sucesso em: {output_path}")
    print("\n=== RECOMENDAÇÕES FINAIS DE COMPRA ===")
    print(
        tabulate(
            top_df[[
                "Ticker",
                "Nome",
                "Preço ($)",
                "Queda 52S (%)",
                "Upside Est. (%)",
                "ROIC (%)",
                "P/E",
                "Veredito de Investimento",
            ]],
            headers="keys",
            tablefmt="rounded_outline",
            showindex=False,
        )
    )