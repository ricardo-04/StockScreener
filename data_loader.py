import os
import io
import time
import requests
import pandas as pd
import yfinance as yf
from typing import List, Dict, Any, Optional

def get_sp500_tickers() -> List[Dict[str, str]]:
    """
    Busca todos os constituintes do S&P 500 (~503 empresas) na Wikipedia.
    Retorna uma lista de dicionários com ticker, security e gics_sector.
    """
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        df_sp = tables[0]
        
        symbol_col = "Symbol" if "Symbol" in df_sp.columns else df_sp.columns[0]
        security_col = "Security" if "Security" in df_sp.columns else df_sp.columns[1]
        sector_col = "GICS Sector" if "GICS Sector" in df_sp.columns else df_sp.columns[2]
        
        records = []
        for _, row in df_sp.iterrows():
            ticker = str(row[symbol_col]).replace(".", "-").strip()
            records.append({
                "ticker": ticker,
                "company_name": str(row[security_col]).strip(),
                "sector": str(row[sector_col]).strip()
            })
        print(f"Sucesso ao descarregar {len(records)} tickers do S&P 500 da Wikipedia.")
        return records
    except Exception as e:
        print(f"Erro ao extrair tickers da Wikipedia: {e}. Usando lista essencial de fallback.")
        fallback_tickers = [
            ("AAPL", "Apple Inc.", "Information Technology"),
            ("MSFT", "Microsoft Corp.", "Information Technology"),
            ("GOOGL", "Alphabet Inc.", "Communication Services"),
            ("AMZN", "Amazon.com Inc.", "Consumer Discretionary"),
            ("NVDA", "Nvidia Corp.", "Information Technology"),
            ("META", "Meta Platforms Inc.", "Communication Services"),
            ("BRK-B", "Berkshire Hathaway", "Financials"),
            ("LLY", "Eli Lilly and Company", "Health Care"),
            ("UNH", "UnitedHealth Group", "Health Care"),
            ("JNJ", "Johnson & Johnson", "Health Care"),
            ("PG", "Procter & Gamble", "Consumer Staples"),
            ("XOM", "Exxon Mobil Corp.", "Energy"),
            ("CVX", "Chevron Corp.", "Energy"),
            ("JPM", "JPMorgan Chase & Co.", "Financials"),
            ("HD", "Home Depot Inc.", "Consumer Discretionary"),
            ("V", "Visa Inc.", "Financials"),
            ("MA", "Mastercard Inc.", "Financials"),
            ("ABBV", "AbbVie Inc.", "Health Care"),
            ("MRK", "Merck & Co.", "Health Care"),
            ("COST", "Costco Wholesale", "Consumer Staples"),
            ("AVGO", "Broadcom Inc.", "Information Technology"),
            ("CAT", "Caterpillar Inc.", "Industrials"),
            ("GE", "General Electric", "Industrials"),
            ("DE", "Deere & Company", "Industrials"),
            ("TXN", "Texas Instruments", "Information Technology"),
            ("QCOM", "Qualcomm Inc.", "Information Technology"),
            ("NEE", "NextEra Energy", "Utilities"),
            ("LIN", "Linde plc", "Materials"),
            ("FCX", "Freeport-McMoRan", "Materials"),
            ("PLD", "Prologis Inc.", "Real Estate"),
            ("AMD", "Advanced Micro Devices", "Information Technology"),
            ("INTC", "Intel Corp.", "Information Technology"),
            ("CSCO", "Cisco Systems", "Information Technology"),
            ("WMT", "Walmart Inc.", "Consumer Staples"),
            ("PEP", "PepsiCo Inc.", "Consumer Staples"),
            ("KO", "Coca-Cola Co.", "Consumer Staples"),
            ("BA", "Boeing Co.", "Industrials"),
            ("UPS", "United Parcel Service", "Industrials"),
            ("RTX", "RTX Corporation", "Industrials"),
            ("HON", "Honeywell International", "Industrials"),
            ("PFE", "Pfizer Inc.", "Health Care"),
            ("BMY", "Bristol-Myers Squibb", "Health Care"),
            ("AMGN", "Amgen Inc.", "Health Care"),
            ("GILD", "Gilead Sciences", "Health Care"),
            ("MCD", "McDonald's Corp.", "Consumer Discretionary"),
            ("SBUX", "Starbucks Corp.", "Consumer Discretionary"),
            ("NKE", "Nike Inc.", "Consumer Discretionary"),
            ("LOW", "Lowe's Companies", "Consumer Discretionary"),
            ("COP", "ConocoPhillips", "Energy"),
            ("SLB", "Schlumberger", "Energy"),
        ]
        return [{"ticker": t, "company_name": n, "sector": s} for t, n, s in fallback_tickers]

def safe_extract_metric(info: dict, keys: list, default=None):
    """Auxiliar para extrair chave de info tentando variações de capitalização."""
    for k in keys:
        if k in info and info[k] is not None:
            return info[k]
    return default

def fetch_ticker_data(item: Dict[str, str], sleep_sec: float = 0.08) -> Optional[Dict[str, Any]]:
    """
    Extrai informações fundamentais, de mercado e demonstrações de um ticker via yfinance.
    Aplica pausa suave configurável para evitar rate-limits de API.
    """
    ticker = item["ticker"]
    company_name = item.get("company_name", ticker)
    sector_hint = item.get("sector", "")
    
    if sleep_sec > 0:
        time.sleep(sleep_sec)

    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or len(info) < 5:
            return None
            
        sector = info.get("sector", sector_hint)
        industry = info.get("industry", "N/A")
        
        # Preços e dados de mercado
        current_price = safe_extract_metric(info, ["currentPrice", "regularMarketPrice", "previousClose"], 0.0)
        fifty_two_high = safe_extract_metric(info, ["fiftyTwoWeekHigh"], 0.0)
        sma_200 = safe_extract_metric(info, ["twoHundredDayAverage", "200DayAverage"], 0.0)
        target_mean_price = safe_extract_metric(info, ["targetMeanPrice", "targetPriceMean"], None)
        
        # Múltiplos e estimativas futuras
        forward_pe = safe_extract_metric(info, ["forwardPE"], None)
        trailing_pe = safe_extract_metric(info, ["trailingPE"], None)
        peg_ratio = safe_extract_metric(info, ["pegRatio", "trailingPegRatio"], None)
        market_cap = safe_extract_metric(info, ["marketCap"], 0.0)
        
        # Rentabilidade Bancária / Institucional
        roe = safe_extract_metric(info, ["returnOnEquity"], None)
        
        # Margens
        gross_margin = safe_extract_metric(info, ["grossMargins"], None)
        operating_margin = safe_extract_metric(info, ["operatingMargins"], None)
        ebitda_margin = safe_extract_metric(info, ["ebitdaMargins"], None)
        
        # Demonstrações e métricas do balanço
        ebitda = safe_extract_metric(info, ["ebitda"], None)
        revenue = safe_extract_metric(info, ["totalRevenue", "revenue"], None)
        fcf = safe_extract_metric(info, ["freeCashflow", "operatingCashflow"], None)
        total_debt = safe_extract_metric(info, ["totalDebt"], 0.0)
        total_cash = safe_extract_metric(info, ["totalCash", "cash"], 0.0)
        current_ratio = safe_extract_metric(info, ["currentRatio"], None)
        
        # EBIT e Despesa de Juros (via Income Statement ou info)
        ebit = safe_extract_metric(info, ["operatingIncome", "ebit"], None)
        interest_expense = None
        
        # Tentar obter dados adicionais através de financial statements
        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                if ebit is None:
                    for row_name in ["Operating Income", "EBIT", "Operating Revenue"]:
                        if row_name in fin.index:
                            ebit = float(fin.loc[row_name].iloc[0])
                            break
                            
                for row_name in ["Interest Expense", "Interest Expense Non Operating", "Net Interest Income"]:
                    if row_name in fin.index:
                        val = float(fin.loc[row_name].iloc[0])
                        interest_expense = abs(val) if val != 0 else None
                        break
        except Exception:
            pass
            
        # Se EBIT continuar nulo, aproximação via Operating Margin * Revenue
        if ebit is None and operating_margin is not None and revenue is not None:
            ebit = operating_margin * revenue
            
        # Balanço Patrimonial para Invested Capital (ROIC)
        invested_capital = None
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                stockholders_equity = None
                total_debt_bs = None
                cash_bs = None
                
                for row in ["Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"]:
                    if row in bs.index:
                        stockholders_equity = float(bs.loc[row].iloc[0])
                        break
                for row in ["Total Debt", "Long Term Debt And Capital Lease Obligation"]:
                    if row in bs.index:
                        total_debt_bs = float(bs.loc[row].iloc[0])
                        break
                for row in ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]:
                    if row in bs.index:
                        cash_bs = float(bs.loc[row].iloc[0])
                        break
                        
                if stockholders_equity is not None:
                    d = total_debt_bs if total_debt_bs is not None else total_debt
                    c = cash_bs if cash_bs is not None else total_cash
                    invested_capital = stockholders_equity + d - c
        except Exception:
            pass

        # Identificação de Serviços Financeiros / Bancos
        sec_str = (str(sector) + " " + str(sector_hint)).lower()
        is_financial = "financial" in sec_str

        return {
            "Ticker": ticker,
            "Company": company_name,
            "Sector": sector,
            "Industry": industry,
            "CurrentPrice": current_price,
            "52WeekHigh": fifty_two_high,
            "SMA200": sma_200,
            "TargetMeanPrice": target_mean_price,
            "ForwardPE": forward_pe,
            "TrailingPE": trailing_pe,
            "PEGRatio": peg_ratio,
            "MarketCap": market_cap,
            "ROE": roe,
            "Revenue": revenue,
            "GrossMargin": gross_margin,
            "OperatingMargin": operating_margin,
            "EBITDA": ebitda,
            "EBIT": ebit,
            "InterestExpense": interest_expense,
            "FCF": fcf,
            "TotalDebt": total_debt,
            "TotalCash": total_cash,
            "CurrentRatio": current_ratio,
            "InvestedCapital": invested_capital,
            "IsFinancial": is_financial
        }
    except Exception as e:
        return None
