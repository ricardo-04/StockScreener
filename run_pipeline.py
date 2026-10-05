import concurrent.futures
import time
import pandas as pd
from data_loader import get_sp500_tickers, fetch_ticker_data
from analyzer import calculate_advanced_metrics, apply_sector_normalization, classify_and_score
from exporter import export_to_institutional_excel

def main():
    print("=" * 75)
    print("INICIANDO PIPELINE DE ANÁLISE INSTITUCIONAL DE AÇÕES - S&P 500 COMPLETO")
    print("=" * 75)

    start_time = time.time()
    
    # 1. Carregar universo total de tickers do S&P 500 (~503 constituintes)
    sp500_list = get_sp500_tickers()
    total_tickers = len(sp500_list)
    print(f"Total de constituintes a processar: {total_tickers}")

    # 2. Extração concorrente com controle de fluxo (pausa suave de 0.08s para estabilidade)
    print("\nExtraindo dados fundamentais e de mercado via yfinance...")
    raw_data = []
    
    # 10 workers com 0.08s de intervalo garantem alta velocidade e resiliência contra rate-limits
    max_workers = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(fetch_ticker_data, item, 0.08): item["ticker"] 
            for item in sp500_list
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                res = future.result()
                if res:
                    raw_data.append(res)
            except Exception as e:
                print(f"Erro ao obter dados de {ticker}: {e}")
            completed += 1
            if completed % 50 == 0 or completed == total_tickers:
                print(f"Progresso: {completed}/{total_tickers} tickers processados ({(completed/total_tickers)*100:.1f}%)...")

    if not raw_data:
        print("Erro crítico: Nenhum dado foi extraído com sucesso.")
        return

    df_raw = pd.DataFrame(raw_data)
    print(f"\nDados obtidos com sucesso para {len(df_raw)} empresas.")
    print("Executando cálculos quantitativos, normalização setorial e arquétipos...")

    # 3. Análise Quantitativa e Normalização Setorial
    df_metrics = calculate_advanced_metrics(df_raw)
    df_normalized = apply_sector_normalization(df_metrics)
    df_final = classify_and_score(df_normalized)

    # 4. Exportação Excel Executivo
    output_excel = "relatorio_investimento_institucional.xlsx"
    print(f"\nGerando Excel executivo '{output_excel}' com formatação aberta...")
    export_to_institutional_excel(df_final, output_excel)

    elapsed_min = round((time.time() - start_time) / 60, 2)
    print("\n" + "=" * 75)
    print(f"PIPELINE CONCLUÍDO COM SUCESSO EM {elapsed_min} MINUTOS!")
    print(f"Relatório gerado: {output_excel}")
    print("=" * 75)

    # Sumário no terminal
    top_summary = df_final[
        (~df_final["Sector"].str.contains("Financial", case=False, na=False)) &
        (df_final["Archetype"] != "Financeiras_Bancos")
    ].sort_values(by="Composite_Score", ascending=False).head(10)
    
    print("\nTOP 10 CANDIDATAS DO SCREENER INSTITUCIONAL (EX-FINANCEIRAS):")
    cols_preview = ["Ticker", "Company", "Sector", "Composite_Score", "Verdict", "Archetype"]
    print(top_summary[cols_preview].to_string(index=False))

    fin_summary = df_final[
        (df_final["Archetype"] == "Financeiras_Bancos") |
        (df_final["Sector"].str.contains("Financial", case=False, na=False))
    ].sort_values(by="Composite_Score", ascending=False).head(5)

    print("\nTOP 5 FINANCEIRAS & BANCOS (AVALIADAS POR ROE & FWD P/E):")
    fin_cols_preview = ["Ticker", "Company", "Composite_Score", "Verdict", "ROE", "ForwardPE"]
    print(fin_summary[fin_cols_preview].to_string(index=False))

if __name__ == "__main__":
    main()
