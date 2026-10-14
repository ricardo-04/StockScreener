@echo off
rem ---------------------------------------------------------------------------
rem StockScreener - atalho de arranque (duplo clique ou linha de comandos).
rem Uso: run_screener.bat [opcoes do main.py]
rem   run_screener.bat                          -> S&P 500 completo, Top 20, com noticias e IA
rem   run_screener.bat --tickers AAPL,MSFT --no-ai
rem   run_screener.bat --universe sp500 --limit 30
rem Cria o ambiente virtual .venv e instala as dependencias na primeira execucao.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
    echo A criar o ambiente virtual .venv e a instalar as dependencias...
    py -3 -m venv .venv || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
    echo AVISO: nao existe .env. Copie .env.example para .env e preencha GEMINI_API_KEY para ativar a analise com IA.
    echo.
)

".venv\Scripts\python.exe" main.py %*
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE% neq 0 echo O screener terminou com erro (codigo %EXIT_CODE%). Consulte o log em output\logs.
echo Prima qualquer tecla para fechar...
pause >nul
endlocal
exit /b %EXIT_CODE%
