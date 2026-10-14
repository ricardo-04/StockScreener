"""Configuração do logging (ficheiro + consola).

O ficheiro guarda tudo (DEBUG); a consola mostra apenas o nível configurado.
Durante os downloads, a barra de progresso (tqdm) e o logging partilham a
consola através de ``tqdm.contrib.logging.logging_redirect_tqdm``.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

CONSOLE_FORMAT = "%(levelname)-8s %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
# Bibliotecas ruidosas: só avisos e erros chegam ao log
NOISY_LOGGERS = ("yfinance", "urllib3", "peewee", "requests", "charset_normalizer", "google_genai", "google", "httpx", "httpcore")


def setup_logging(log_dir: str | Path, level: str = "INFO", run_label: str = "screener") -> Path:
    """Configura o logger raiz com um ficheiro por execução e saída na consola.

    Devolve o caminho do ficheiro de log.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{run_label}_{datetime.now():%Y-%m-%d_%H%M%S}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):  # evita handlers duplicados em re-execuções
        root.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return log_file
