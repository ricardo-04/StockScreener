"""Cache em disco (SQLite) com TTL para os dados descarregados.

Cada entrada é um JSON identificado por uma chave (ex.: ``ticker:AAPL``,
``universe:sp500``) com a data de criação. Uma entrada mais antiga do que o TTL
é tratada como inexistente; ``--refresh-cache`` ignora a cache por completo.
Seguro para uso a partir de várias threads (lock interno).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class DiskCache:
    """Cache chave -> JSON em SQLite, com validade (TTL) em horas."""

    def __init__(self, path: str | Path, ttl_hours: float = 24.0) -> None:
        self.path = Path(path)
        self.ttl_hours = float(ttl_hours)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                "key TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            self._conn.commit()

    # ------------------------------------------------------------------ leitura
    def get(self, key: str, ttl_hours: float | None = None, ignore_ttl: bool = False) -> dict[str, Any] | None:
        """Devolve o payload se existir e estiver dentro do TTL; caso contrário ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at, payload FROM entries WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        created_at = datetime.fromisoformat(row[0])
        if not ignore_ttl and self._is_expired(created_at, ttl_hours):
            return None
        try:
            return json.loads(row[1])
        except json.JSONDecodeError:
            log.warning("Entrada de cache corrompida (%s); será descartada", key)
            self.delete(key)
            return None

    def age_hours(self, key: str) -> float | None:
        with self._lock:
            row = self._conn.execute("SELECT created_at FROM entries WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        created_at = datetime.fromisoformat(row[0])
        return (datetime.now(timezone.utc) - created_at).total_seconds() / 3600.0

    # ------------------------------------------------------------------ escrita
    def set(self, key: str, payload: dict[str, Any], created_at: datetime | None = None) -> None:
        """Grava (ou substitui) uma entrada. ``created_at`` só deve ser usado em testes."""
        stamp = (created_at or datetime.now(timezone.utc)).isoformat()
        text = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO entries (key, created_at, payload) VALUES (?, ?, ?)",
                (key, stamp, text),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM entries WHERE key = ?", (key,))
            self._conn.commit()

    def purge_expired(self, ttl_hours: float | None = None) -> int:
        """Remove entradas expiradas; devolve quantas foram apagadas."""
        with self._lock:
            rows = self._conn.execute("SELECT key, created_at FROM entries").fetchall()
            expired = [key for key, created in rows if self._is_expired(datetime.fromisoformat(created), ttl_hours)]
            if expired:
                self._conn.executemany("DELETE FROM entries WHERE key = ?", [(k,) for k in expired])
                self._conn.commit()
        return len(expired)

    # ------------------------------------------------------------------ utilidades
    def stats(self, ttl_hours: float | None = None) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT created_at FROM entries").fetchall()
        fresh = sum(1 for (created,) in rows if not self._is_expired(datetime.fromisoformat(created), ttl_hours))
        size = self.path.stat().st_size if self.path.exists() else 0
        return {"entries": len(rows), "fresh": fresh, "expired": len(rows) - fresh, "size_bytes": size}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "DiskCache":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _is_expired(self, created_at: datetime, ttl_hours: float | None) -> bool:
        ttl = self.ttl_hours if ttl_hours is None else float(ttl_hours)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - created_at > timedelta(hours=ttl)
