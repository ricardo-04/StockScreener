# StockScreener — screener de ações em funil

> **Aviso:** isto é uma ferramenta de **triagem quantitativa** para reduzir um universo
> grande de empresas a uma lista curta que merece análise humana. **Não é uma
> recomendação de investimento** nem substitui a leitura dos relatórios e contas.
> Os dados vêm do Yahoo Finance (via `yfinance`) e podem conter erros ou atrasos.

## O que faz

Um universo de empresas (por omissão o S&P 500) passa por filtros sequenciais, como
fazem investidores profissionais. Cada empresa é eliminada na **primeira etapa em que
falha** e fica registado o motivo (regra, valor observado e limiar). No fim, as
sobreviventes são ordenadas por um score composto e devolve-se um **Top N** com uma
explicação em texto. Opcionalmente, o Top N é enriquecido com notícias e uma análise
qualitativa gerada por IA (Gemini), claramente identificada como tal e sem poder para
alterar a aprovação do funil.

```
Universo (503) → Etapa 1 Elegibilidade → Etapa 2 Saúde financeira → Etapa 3 Qualidade
→ Etapa 4 Crescimento → Etapa 5 Valuation (relativa ao setor) → Etapa 6 Momentum
→ Etapa 7 Ranking → Top 20 + explicação (+ notícias + IA)
```

## Estado do projeto

| Fase | Conteúdo | Estado |
|------|----------|--------|
| 1 | Fundação: estrutura, `config.yaml`, CLI, universo, download concorrente com retry e cache, métricas base com as correções de dados, motor do funil, logging, testes | **concluída** |
| 2 | Etapas 1-6 do funil, Piotroski / Altman / Beneish, métricas multi-ano, ranking (Etapa 7) e explicações sem IA | **concluída** |
| 3 | Notícias (yfinance / Finnhub) e análise qualitativa com Gemini para o Top N | **concluída** |
| 4 | Relatórios Excel completos e Markdown, CLI final, README final | **concluída** |

## Instalação

Requer Python 3.11+ (desenvolvido com 3.12).

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
```

## Configuração

- **`config.yaml`** — universo por omissão, parâmetros de download e cache, classificação por
  tipo de negócio, todos os limiares de todas as etapas, pesos do ranking, Top N e opções de
  IA. Não há números mágicos no código: altere aqui.
- **`.env`** — chaves de API (copie `.env.example` para `.env`; o `.env` está no `.gitignore`):
  - `GEMINI_API_KEY` para a análise qualitativa (SDK `google-genai`); sem chave ou com
    `--no-ai` o pipeline corre na mesma sem essa etapa;
  - `GEMINI_MODEL` (opcional) sobrepõe-se a `ai.model` do `config.yaml`;
  - `FINNHUB_API_KEY` (opcional) para notícias via Finnhub; sem chave usa-se só o `yfinance`.

## Utilização

**Atalho (Windows):** faça duplo clique em `run_screener.bat` (ou num atalho para ele no Ambiente de
Trabalho). Cria o `.venv` e instala as dependências na primeira execução, corre o screener com as
opções por omissão do `config.yaml` e mantém a janela aberta no fim. Aceita os mesmos argumentos do
`main.py`: `run_screener.bat --tickers AAPL,MSFT --no-ai`.

```bash
python main.py                                  # S&P 500 completo (config universe.default)
python main.py --universe sp500 --top 20
python main.py --tickers AAPL,MSFT,ASML --no-ai
python main.py --no-news                        # sem notícias (e sem IA)
python main.py --universe sp500 --limit 30      # validação rápida com os primeiros 30 tickers
python main.py --universe lista.csv             # CSV custom com coluna "ticker"
python main.py --refresh-cache                  # ignora a cache e descarrega tudo de novo
python main.py --skip-stage momentum            # ignora uma etapa (repetível: --skip-stage growth --skip-stage momentum)
python main.py --top 5                          # validação barata da IA: 5 chamadas, dados e notícias da cache
python main.py --help
```

Universos disponíveis: `sp500`, `sp400`, `sp600`, `sp1500` (união dos três) ou um CSV com uma
coluna `ticker` (ou a primeira coluna). Opções adicionais: `--config` (YAML alternativo),
`--max-workers`, `--output-dir`, `--log-level`, `--version`.

Saídas (com data no nome, em `output/`):

- `output/screener_YYYY-MM-DD.xlsx` — folhas `Resumo_Funil` (contagens por etapa e regras que mais
  eliminaram), `Top_N` (ranking, score por pilar, métricas principais, alertas, explicação,
  nº de notícias, sentimento da IA e marca "REVER MANUALMENTE"), `Noticias_IA` (por empresa do
  Top N: a análise da IA e uma linha por notícia), `Eliminadas` (ticker, etapa, regra, valor
  observado, limiar) e `Universo_Completo` (todas as métricas, percentis e z-scores setoriais);
- `output/relatorio_YYYY-MM-DD.md` — relatório Markdown legível: aviso, resumo do funil, tabela do
  Top N e uma secção por empresa (score e pilares, explicação por regras, métricas com posição no
  setor, análise da IA identificada como tal e notícias recentes com links);
- `output/logs/screener_<data_hora>.log` — log completo (DEBUG) de cada execução.

Os ficheiros do mesmo dia são substituídos; se o Excel estiver aberto, é gravada uma cópia com a hora no nome.

## As etapas do funil

Antes de tudo, cada empresa é classificada por tipo de negócio a partir de `sector` +
`industry` do yfinance: `standard` (default), `bank_insurance` (bancos, seguradoras,
gestoras de ativos, capital markets), `reit`, `utility` e `cyclical_commodity` (Energy,
Basic Materials). Visa/Mastercard, bolsas, fornecedores de dados financeiros e corretores
de seguros são avaliados como empresas normais. Cada etapa pode ter limiares diferentes
por tipo (ver `config.yaml`).

| Etapa | Nome | Regras principais (limiares no `config.yaml`) |
|-------|------|-----------------------------------------------|
| 0 | Universo e dados | Lista de tickers; falhas de download ficam registadas como eliminações com motivo |
| 1 | Elegibilidade e dados | Market cap ≥ 2 mil M USD; volume médio diário (60 d) ≥ 10 M USD; preço ≥ 5 USD; ≥ 3 anos de demonstrações e campos críticos disponíveis ("dados insuficientes: …") |
| 2 | Saúde financeira | Net Debt/EBITDA (3.0 standard, 2.0 cíclicas, 5.5 utilities, 7.0 REITs); cobertura de juros ≥ 4 ou sem dívida; Altman Z ≥ 1.8; current ratio ≥ 1.0; Piotroski ≥ 5; Beneish M ≤ −1.78 (aviso se não calculável); bancos: ROE positivo 3 anos e P/B disponível |
| 3 | Qualidade | ROIC médio 3 anos ≥ 10%; FCF positivo em ≥ 3 dos últimos 4 anos; conversão de caixa ≥ 0.7; estabilidade da margem bruta; diluição ≤ 3%/ano; bancos: ROE médio ≥ 10% |
| 4 | Crescimento | CAGR receita 3 anos ≥ 3% (cíclicas ≥ 0%); EPS sem 2 quedas seguidas; crescimento esperado não negativo |
| 5 | Valuation (relativa ao setor) | FCF yield ≥ percentil 40 do setor; EV/EBIT ≤ percentil 70; Forward P/E entre 0 e 40; bancos: P/B ≤ percentil 70 e Forward P/E ≤ 18; PEG > 2.5 só aviso |
| 6 | Momentum (desligável) | Momentum 12-1 > −20%; "faca a cair" se preço < SMA200 e momentum < −10% |
| 7 | Ranking | Score 0-100 com z-scores dentro do setor (winsorizados a ±3): Qualidade 35%, Valuation 30%, Saúde financeira 20%, Momentum 15% |

Notas sobre a aplicação das regras:

- **Dados em falta** numa métrica crítica eliminam com o motivo "dados insuficientes: <campo>";
  em métricas secundárias (Altman, current ratio, Beneish, PEG, estimativas) geram apenas um aviso,
  que aparece na coluna `funnel_warnings` e nas explicações.
- **Percentis setoriais** (FCF yield, EV/EBIT, P/B) são calculados sobre o universo completo,
  dentro do `sector` do yfinance. Em setores com menos de `valuation.min_sector_size` empresas no
  universo (por exemplo, numa execução com `--tickers`), as regras relativas ao setor passam a aviso.
- **Sem juros reportados mas com dívida**: a cobertura de juros não é calculável; se a dívida for
  imaterial (caixa líquida ou Net Debt/EBITDA ≤ 1), fica só o aviso; caso contrário elimina.
- **Lenders em "Credit Services"** (American Express, Capital One): sem EBIT e com margem
  financeira relevante são reclassificados como `bank_insurance` (heurística configurável em
  `business_types.lender_heuristic`); Visa e Mastercard têm EBIT e ficam `standard`.
- **REITs**: usam "Purchase Of Investment Properties" como CapEx quando não há "Capital Expenditure";
  com os limiares por omissão (ROIC ≥ 10%, FCF positivo) tendem a ser eliminados na Etapa 3,
  ajuste `stages.quality.by_type.reit` se quiser tratá-los de forma mais branda.

### Ranking e explicações

O score composto usa z-scores **robustos** (mediana/MAD) calculados dentro do setor sobre o
universo completo, winsorizados a ±3, agregados por pilar (média das métricas aplicáveis ao tipo
de negócio) e combinados com os pesos do `config.yaml`. 50 pontos correspondem à mediana do setor.
As métricas de cada pilar e a sua direção ("maior é melhor" ou "menor é melhor") estão em
`scoring.pillars`.

Para cada empresa do Top N, `src/explain.py` gera um parágrafo sem IA com os 3 pontos mais fortes e
os 2 mais fracos relativamente ao setor (por percentil; em setores pequenos usa a posição ordinal)
mais os alertas do funil, por exemplo: "Passou todas as 6 etapas. Pontos fortes: caixa líquida,
ROIC médio 3 anos de 32.0% (top 25% do setor), ... Pontos de atenção: PEG de 2.0 (...)".

## Decisões de dados (correções face à versão anterior)

- **FCF = Operating Cash Flow − |CapEx|** a partir da demonstração de fluxos de caixa. Se
  faltar o CapEx, o FCF fica NaN (não se usa o OCF como substituto).
- **Despesa de juros**: `Interest Expense` ou `Interest Expense Non Operating`; em último
  recurso, `Interest Paid Supplemental Data` dos fluxos de caixa. `Net Interest Income`
  nunca é usado (é margem financeira, não despesa).
- **EBIT**: `Operating Income` (preferido) ou `EBIT`. `Operating Revenue` nunca é usado.
- **Cobertura de juros** sem despesa de juros fica NaN. "Sem dívida" é um estado explícito
  (`debt_status = sem_divida`), não um número arbitrário.
- **Dados em falta** não são preenchidos com medianas nem percentil 50: ficam NaN e eliminam a
  empresa na Etapa 1 com o motivo "dados insuficientes: <campo>".
- **Lacunas do Yahoo com solução de princípio** (sempre registadas nas colunas `*_source` e
  `data_notes`): CapEx em falta num ano usa "Purchase Of PPE" desse ano; ativo total em falta é
  derivado de passivo + capital próprio; despesa de juros em falta só no último ano usa a do ano
  anterior (com aviso no funil); sem qualquer rubrica de dívida no balanço nem juros nos
  resultados, a dívida é assumida 0 (o Yahoo omite rubricas a zero).
- **Falhas de download** são registadas no log e na lista de eliminadas com ticker e motivo.
- O `dividendYield` do yfinance vem em percentagem e é convertido para fração.
- Métricas multi-ano: as demonstrações são guardadas com 4 anos fiscais e expandidas em
  colunas `*_fy0` (mais recente) a `*_fy3`.

## Notícias e análise qualitativa com IA (só para o Top N)

- **Notícias** (`src/news.py`): fonte gratuita `yfinance` (`Ticker.news`, formato novo com os campos
  em `content`, e `Search(ticker).news`, formato antigo, como fallback) e, se existir
  `FINNHUB_API_KEY`, o endpoint `company-news` da Finnhub dos últimos 30 dias. Guarda título,
  fonte, data, link e resumo; remove duplicados (mesmo link ou mesmo título); no máximo 15 por
  empresa, mais recentes primeiro; cache de 6 h.
- **Gemini** (`src/ai/gemini_analyst.py`, SDK oficial `google-genai`): para cada empresa do Top N
  envia as métricas principais, a explicação do funil e as notícias numeradas (`[N1]`, `[N2]`, …) e
  pede JSON estrito (structured output) com `sentiment`, `sentiment_score`, `catalysts`, `risks`,
  `red_flags`, `moat_assessment`, `thesis_summary` e `news_impact`. A instrução de sistema obriga a
  usar apenas a informação fornecida, a citar a notícia que suporta cada ponto e a escrever
  "informação insuficiente" quando for o caso.
- **A IA nunca altera a aprovação do funil.** Se devolver red flags (investigação regulatória,
  processo judicial, saída de CEO/CFO, profit warning, restatement, …) a empresa fica marcada
  "REVER MANUALMENTE" no `Top_N`, na folha `Noticias_IA` e na consola.
- Respostas em cache por (modelo, ticker, data) em `cache/ai/`; rate limiting entre chamadas e
  retry com backoff em 429/5xx. Modelo Flash por omissão (custo); `thinking_budget: 0` no
  `config.yaml` desliga o raciocínio prolongado. Se o modelo configurado já não existir e a API
  sugerir outro ("use models/..."), o analista adota o sugerido; se o modelo estiver sobrecarregado
  (503) depois das tentativas, passa aos `ai.fallback_models` pela ordem configurada. O modelo
  efetivamente usado fica no log e na coluna `ai_model`.
- Uma falha da IA numa empresa (erro de API, JSON inválido) fica registada em `ai_error` e não
  interrompe o pipeline nem fica em cache.
- Tudo o que vem da IA está identificado como tal (colunas `ai_*`); as explicações do funil
  continuam a ser geradas por regras, sem IA.

## Cache, concorrência e logging

- Os dados do yfinance (info, demonstrações, preços, estimativas) ficam numa cache SQLite em
  `cache/yfinance_cache.sqlite` com TTL de 24 h (configurável). As listas de índices têm TTL de
  7 dias e funcionam offline depois da primeira execução.
- Download concorrente com `ThreadPoolExecutor` (máx. 8 workers por omissão), retry com backoff
  exponencial (mais lento em caso de rate-limit do Yahoo) e barra de progresso `tqdm`.
- Logging com o módulo `logging`: consola (nível configurável) e ficheiro por execução.

## Estrutura do projeto

```
config.yaml              # universo, limiares de todas as etapas, pesos, top_n, opções de IA
main.py                  # CLI (argparse)
src/
  config.py              # leitura/validação do config.yaml
  logging_setup.py       # logging ficheiro + consola
  universe.py            # S&P 500/400/600 da Wikipedia, CSV custom, lista inline
  data/fetcher.py        # yfinance: info, demonstrações (4 anos), preços (2 anos), estimativas
  data/cache.py          # cache SQLite com TTL
  metrics/               # funções puras (uma por métrica), sem I/O
    statements.py        # acesso uniforme às demonstrações
    cashflow.py          # FCF
    leverage.py          # dívida líquida, ND/EBITDA, cobertura de juros, debt_status
    profitability.py     # margens, taxa de imposto efetiva
    quality.py           # ROIC, ROE, conversão de caixa, margem bruta, diluição
    growth.py            # CAGR, quedas consecutivas de EPS
    scores.py            # Piotroski F, Altman Z, Beneish M
    valuation.py         # FCF yield, EV/EBIT, FFO, P/FFO
    prices.py            # volume em dólares, SMA, momentum 12-1
    sector.py            # percentis, z-scores robustos e medianas por setor
    classification.py    # tipo de negócio (standard, bank_insurance, reit, utility, cyclical)
    builder.py           # tabela de métricas por empresa
  funnel/base.py         # classes base das etapas, regras, eliminações, registo
  funnel/stages.py       # etapas 1-6 (elegibilidade, saúde, qualidade, crescimento, valuation, momentum)
  funnel/engine.py       # corre as etapas em sequência e regista eliminações
  scoring.py             # Etapa 7: ranking final por pilares
  explain.py             # explicações em texto, sem IA
  news.py                # notícias (yfinance / Finnhub) para o Top N
  ai/gemini_analyst.py   # análise qualitativa com Gemini (structured output, cache, retry)
  report/excel.py        # exportação Excel (5 folhas)
  report/markdown.py     # relatório Markdown com uma secção por empresa do Top N
tests/                   # pytest com dados sintéticos
```

## Testes

```bash
pytest
```

Os testes usam demonstrações sintéticas e não acedem à rede.
