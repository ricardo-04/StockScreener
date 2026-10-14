# TODO list — StockScreener v2

## Fase 1 — Limpeza e base (sem isto, o resto não vale a pena)
- [ ] Apagar `institutional_screener.py` (duplica a lógica do pipeline com regras diferentes) e aproveitar só o que tiver de útil (FCF = OCF − CapEx, cálculo de Net Income).
- [ ] Remover o dicionário `specific_cases` do `exporter.py` (vereditos escritos à mão para MMM, NVDA, ADBE…). Isto não é análise: é opinião fixa que fica desatualizada.
- [ ] Criar `requirements.txt`, `README.md`, `config.yaml` (todos os limiares lá, nada hardcoded) e pôr `*.xlsx` e `cache/` no `.gitignore`.
- [ ] Cache local dos dados do yfinance (parquet/sqlite, validade de 24h) para não fazer ~1500 pedidos de cada vez que corres o pipeline.
- [ ] Corrigir bugs de dados (lista no prompt abaixo).

## Fase 2 — Funil de eliminação (o coração do projeto)
- [ ] Estrutura em etapas sequenciais: cada empresa que falha fica registada com a **etapa** e o **motivo exato** (ex.: `Etapa 2 – Solvência: Net Debt/EBITDA = 4.1x > 3.0x`).
- [ ] Etapa 0 Universo → 1 Liquidez/Dimensão → 2 Saúde financeira → 3 Qualidade → 4 Crescimento → 5 Valuation → 6 Momentum → 7 Ranking final (Top N).
- [ ] Regras específicas por setor (Bancos/Seguradoras, REITs, Utilities) em vez de uma regra única para todos.
- [ ] Métricas multi-ano (3–4 anos), não só o último ano: ROIC médio, FCF positivo em X de Y anos, CAGR de receita, diluição de ações.
- [ ] Scores clássicos: Piotroski F-Score, Altman Z-Score, Beneish M-Score (deteção de manipulação contabilística).
- [ ] Explicação automática em texto (sem IA) para cada empresa aprovada: porque passou e quais são os pontos fracos.

## Fase 3 — Notícias e IA (só no Top N)
- [ ] Notícias por empresa (yfinance `.news` gratuito; opcional Finnhub com API key).
- [ ] Gemini analisa notícias + métricas e devolve JSON estruturado: sentimento, catalisadores, riscos, red flags, resumo da tese.
- [ ] A IA **não** pode aprovar empresas que falharam no funil; só acrescenta contexto e pode marcar alertas.

## Fase 4 — Output e qualidade
- [ ] Excel com abas: Resumo do Funil, Top N com explicação, Eliminadas (com motivo), Notícias/IA, Universo completo.
- [ ] Relatório Markdown/HTML legível por empresa.
- [ ] CLI (`python main.py --universe sp500 --top 20 --no-ai`).
- [ ] Testes unitários para os cálculos (ROIC, Piotroski, Altman, filtros).

---

# PROMPT PARA COLAR NO GEMINI CLI

> Recomendo correr por fases: cola o prompt inteiro, mas pede para começar só pela Fase 1 e só avançar quando confirmares que funciona. Depois diz "avança para a Fase 2", e assim sucessivamente.

```
És um engenheiro de software sénior com experiência em equity research quantitativo. Vais refatorar este projeto Python (StockScreener) num screener de ações em formato de FUNIL, como fazem investidores profissionais: um universo grande de empresas passa por filtros sequenciais e cada empresa é eliminada na primeira etapa em que falha, ficando registado o motivo. No fim devolve um Top N de empresas que passaram TODOS os filtros, com explicação, e (opcionalmente) notícias e análise qualitativa com Gemini.

Lê primeiro todos os ficheiros do projeto (run_pipeline.py, data_loader.py, analyzer.py, exporter.py, institutional_screener.py) antes de alterar qualquer coisa. Trabalha POR FASES. Faz só a Fase 1, mostra-me o resumo das alterações e espera pela minha confirmação antes de avançar para a fase seguinte. Código e comentários em português, nomes de variáveis em inglês.

=====================================================================
PROBLEMAS ATUAIS QUE TENS DE CORRIGIR
=====================================================================
1. Existem duas implementações paralelas com lógica diferente (institutional_screener.py vs run_pipeline+analyzer+exporter). Unifica numa só arquitetura e apaga institutional_screener.py.
2. exporter.py tem o dicionário `specific_cases` com vereditos escritos à mão por ticker (MMM, NVDA, ADBE, DECK...). REMOVE por completo. Nenhum ticker pode ter tratamento hardcoded.
3. A checklist "qualitativa" genérica (moat/mgmt/regulatory) é derivada de métricas quantitativas e apresentada como se fosse análise qualitativa. Remove-a; a parte qualitativa passa a vir do módulo de IA (Fase 3) e fica claramente identificada como tal.
4. Bugs de dados em data_loader.py:
   - FCF faz fallback para `operatingCashflow` → sobrestima o FCF. FCF tem de ser Operating Cash Flow − CapEx (do cashflow statement); se faltar, o valor fica NaN (não inventar).
   - "Net Interest Income" está a ser usado como despesa de juros e "Operating Revenue" como fallback de EBIT. Ambos errados; remove.
   - Interest coverage = 25 quando não há despesa de juros é arbitrário. Usa NaN e trata explicitamente "sem dívida" como caso próprio.
   - Os percentis setoriais fazem fillna(50): isto recompensa dados em falta. Dados em falta em métricas críticas devem eliminar a empresa na Etapa 1 (completude de dados) com motivo "dados insuficientes: <campos>".
   - Exceções são engolidas com `return None` sem registo. Regista sempre ticker + motivo num log e na lista de eliminadas.
   - O setor "Financial Services" do yfinance inclui Visa/Mastercard/processadores de pagamentos, que devem ser avaliados como empresas normais. Separa por `industry`: só bancos, seguradoras, asset managers e capital markets vão para o tratamento de financeiras.
5. Tudo usa apenas o último ano fiscal. Profissionais olham para consistência ao longo de vários anos.
6. Limiares e pesos estão espalhados pelo código como números mágicos.
7. Sem cache: cada execução faz ~1500 pedidos ao yfinance e arrisca rate-limit.

=====================================================================
FASE 1 — FUNDAÇÃO
=====================================================================
Estrutura alvo:
  stockscreener/
    config.yaml            # universo, limiares de todas as etapas, pesos, top_n, opções de IA
    main.py                # CLI com argparse
    src/
      universe.py          # obter tickers (S&P 500 da Wikipedia; preparado para S&P 400/600 e listas custom em CSV)
      data/fetcher.py      # yfinance: info, financials, balance_sheet, cashflow (anuais, 4 anos), histórico de preços 1-2 anos
      data/cache.py        # cache em disco (parquet ou sqlite) com TTL configurável (default 24h)
      metrics/             # funções puras de cálculo (uma por métrica), sem I/O
      funnel/stages.py     # cada etapa = classe/função com nome, regras e motivo de eliminação
      funnel/engine.py     # corre as etapas em sequência e regista eliminações
      scoring.py           # ranking final das sobreviventes
      explain.py           # explicação em texto, baseada em regras (sem IA)
      news.py              # Fase 3
      ai/gemini_analyst.py # Fase 3
      report/excel.py      # exportação
      report/markdown.py
    tests/
    requirements.txt
    README.md
    .env.example           # GEMINI_API_KEY=, FINNHUB_API_KEY= (opcional)

- Download concorrente com ThreadPoolExecutor (máx. ~8 workers), retry com backoff exponencial, e barra de progresso (tqdm).
- Logging com o módulo `logging` (ficheiro + consola), não prints soltos.
- Adiciona *.xlsx, cache/, .env, output/ ao .gitignore e remove o .xlsx do repositório.

=====================================================================
FASE 2 — O FUNIL (sem IA, sem API keys)
=====================================================================
Cada etapa recebe o DataFrame das sobreviventes e devolve (aprovadas, eliminadas). Cada eliminada tem: ticker, etapa, regra que falhou, valor observado, limiar. Todos os limiares vêm do config.yaml. Uma empresa é eliminada na PRIMEIRA etapa que falha, mas regista todas as regras falhadas dessa etapa.

Classificação prévia por tipo de negócio (usar sector + industry do yfinance):
  - "standard" (default)
  - "bank_insurance" (bancos, seguradoras, asset managers, capital markets)
  - "reit" (Real Estate)
  - "utility"
  - "cyclical_commodity" (Energy, Basic Materials)
Cada etapa pode ter limiares diferentes por tipo (definidos no config).

ETAPA 0 – Universo: lista de tickers (configurável).

ETAPA 1 – Elegibilidade e dados:
  - Market cap ≥ 2 mil M USD
  - Volume médio diário em dólares (60 dias) ≥ 10 M USD
  - Preço ≥ 5 USD
  - Pelo menos 3 anos de demonstrações financeiras e os campos críticos disponíveis

ETAPA 2 – Saúde financeira (kill switches):
  - standard/cyclical: Net Debt/EBITDA ≤ 3.0 (cyclical ≤ 2.0); Interest Coverage (EBIT/juros) ≥ 4 ou sem dívida; Altman Z-Score ≥ 1.8; Current ratio ≥ 1.0
  - utility: Net Debt/EBITDA ≤ 5.5; Interest coverage ≥ 2.5
  - bank_insurance: ignora métricas de dívida industrial; exige ROE positivo nos últimos 3 anos e P/B disponível
  - reit: Net Debt/EBITDA ≤ 7
  - Todos (exceto bancos): Piotroski F-Score ≥ 5; Beneish M-Score ≤ -1.78 (se calculável; se não, só aviso)

ETAPA 3 – Qualidade do negócio:
  - ROIC médio 3 anos ≥ 10% (NOPAT = EBIT × (1 − taxa efetiva limitada entre 10% e 30%); Capital Investido = Equity + Dívida − Caixa)
  - FCF positivo em pelo menos 3 dos últimos 4 anos
  - Conversão de caixa FCF/Lucro Líquido médio ≥ 0.7
  - Estabilidade da margem bruta: desvio padrão 4 anos ≤ 5 p.p. (aviso, não elimina, em cyclical)
  - Diluição: crescimento anual de nº de ações ≤ 3%
  - bank_insurance: ROE médio 3 anos ≥ 10%

ETAPA 4 – Crescimento:
  - CAGR de receita 3 anos ≥ 3% (cyclical: ≥ 0%)
  - EPS não pode ter caído em 2 anos seguidos
  - Crescimento de receita esperado (se disponível) não negativo

ETAPA 5 – Valuation (relativa ao setor, para não comparar software com bancos):
  - standard/cyclical/utility: FCF Yield (FCF/EV) ≥ percentil 40 do setor E EV/EBIT ≤ percentil 70 do setor E Forward P/E entre 0 e 40
  - bank_insurance: P/B ≤ percentil 70 do setor e Forward P/E ≤ 18
  - reit: dividend yield e P/FFO aproximado (se não houver dados, só aviso)
  - PEG ≤ 2.5 quando disponível (aviso, não elimina)

ETAPA 6 – Momentum / timing (configurável, pode ser desligada):
  - Momentum 12-1 meses (retorno de 12 meses excluindo o último) > -20%
  - Não eliminar só por estar abaixo da SMA200, mas marcar como "faca a cair" se preço < SMA200 E momentum 12-1 < -10%

ETAPA 7 – Ranking final das sobreviventes:
  - Score composto 0-100 com z-scores calculados DENTRO do setor (winsorizados a ±3) em 4 pilares: Qualidade (35%), Valuation (30%), Saúde Financeira (20%), Momentum (15%). Pesos no config.
  - Devolve Top N (default 20).

Explicações (explain.py, sem IA): para cada empresa do Top N gera um parágrafo com base nas métricas, ex.:
  "Passou todas as 6 etapas. Pontos fortes: ROIC médio de 24% (top 15% do setor), caixa líquida, FCF yield de 6.1%. Pontos de atenção: crescimento de receita de 3.2% (abaixo da mediana do setor), PEG de 2.3."
Inclui sempre os 3 pontos mais fortes e os 2 mais fracos relativamente ao setor.

Também gera um resumo do funil: "503 empresas → Etapa 1: 471 → Etapa 2: 352 → ... → 27 sobreviventes → Top 20", com as 5 regras que mais eliminaram.

Escreve testes em tests/ (pytest) com dados sintéticos para: ROIC, FCF, Piotroski, Altman Z, Beneish, e para o motor do funil (garantir que a etapa e o motivo de eliminação ficam corretos).

=====================================================================
FASE 3 — NOTÍCIAS + ANÁLISE COM GEMINI (só para o Top N)
=====================================================================
news.py:
  - Fonte gratuita por defeito: yfinance `Ticker.news` (atenção: a estrutura do JSON mudou em versões recentes; os campos podem estar dentro de `content`. Trata ambos os formatos).
  - Fonte opcional se existir FINNHUB_API_KEY: endpoint company-news dos últimos 30 dias.
  - Guarda título, fonte, data, link e resumo. Remove duplicados. Máx. 15 notícias por empresa, ordenadas por data.

ai/gemini_analyst.py:
  - SDK oficial `google-genai`; chave em GEMINI_API_KEY via .env (python-dotenv). Nome do modelo configurável no config.yaml / env GEMINI_MODEL (usa o modelo Flash atual por defeito, por custo).
  - Se não houver chave ou --no-ai, o pipeline corre na mesma sem esta etapa.
  - Para cada empresa do Top N envia: métricas principais, explicação do funil e as notícias. Pede resposta em JSON estrito (usa response schema / structured output) com:
      {
        "sentiment": "positivo|neutro|negativo",
        "sentiment_score": -1.0 a 1.0,
        "catalysts": [lista curta],
        "risks": [lista curta],
        "red_flags": [ex.: investigação regulatória, processo judicial, saída de CEO/CFO, profit warning, restatement],
        "moat_assessment": "texto curto",
        "thesis_summary": "3-4 frases",
        "news_impact": "como as notícias podem afetar o preço a curto/médio prazo"
      }
  - Instrução de sistema: basear-se APENAS nas notícias e métricas fornecidas, não inventar factos, citar a notícia que suporta cada risco/catalisador, e dizer "informação insuficiente" quando for o caso.
  - A IA NÃO altera a aprovação do funil. Se encontrar red_flags, a empresa fica marcada "REVER MANUALMENTE" no relatório.
  - Cache das respostas por (ticker, data) para não gastar chamadas repetidas. Rate limiting simples entre chamadas.

=====================================================================
FASE 4 — RELATÓRIOS E CLI
=====================================================================
Excel (openpyxl, aproveita a formatação atual do exporter.py):
  1. "Resumo_Funil": contagens por etapa e top regras de eliminação
  2. "Top_N": ranking, score, pilares, métricas principais, explicação em texto, sentimento IA, alertas
  3. "Noticias_IA": por empresa, notícias + análise do Gemini
  4. "Eliminadas": ticker, etapa, regra, valor, limiar
  5. "Universo_Completo": todas as métricas calculadas
Markdown: output/relatorio_YYYY-MM-DD.md com uma secção por empresa do Top N.
Ficheiros de output com data no nome dentro de output/.

CLI:
  python main.py --universe sp500 --top 20
  python main.py --tickers AAPL,MSFT,ASML --no-ai
  python main.py --refresh-cache
  python main.py --skip-stage momentum

Atualiza o README com: o que faz, como instalar, como configurar API keys, explicação de cada etapa do funil e um aviso de que isto é uma ferramenta de triagem e não recomendação de investimento.

No final de cada fase: corre os testes, corre o pipeline com um universo pequeno (ex.: 30 tickers) para validar, e mostra-me o output e o resumo do funil.
```
