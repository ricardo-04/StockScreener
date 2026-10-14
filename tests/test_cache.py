"""Cache SQLite com TTL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data.cache import DiskCache


def test_set_get_roundtrip(tmp_path):
    with DiskCache(tmp_path / "c.sqlite", ttl_hours=24) as cache:
        cache.set("ticker:AAPL", {"info": {"marketCap": 1}, "warnings": ["x"], "nan": None})
        assert cache.get("ticker:AAPL") == {"info": {"marketCap": 1}, "warnings": ["x"], "nan": None}
        assert cache.get("ticker:MSFT") is None
        assert 0 <= cache.age_hours("ticker:AAPL") < 1
        assert cache.age_hours("ticker:MSFT") is None


def test_ttl_expiry_and_ignore_ttl(tmp_path):
    with DiskCache(tmp_path / "c.sqlite", ttl_hours=24) as cache:
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        cache.set("ticker:OLD", {"v": 1}, created_at=old)
        assert cache.get("ticker:OLD") is None
        assert cache.get("ticker:OLD", ignore_ttl=True) == {"v": 1}
        assert cache.get("ticker:OLD", ttl_hours=48) == {"v": 1}


def test_purge_and_stats(tmp_path):
    with DiskCache(tmp_path / "c.sqlite", ttl_hours=1) as cache:
        cache.set("fresh", {"v": 1})
        cache.set("stale", {"v": 2}, created_at=datetime.now(timezone.utc) - timedelta(hours=5))
        stats = cache.stats()
        assert (stats["entries"], stats["fresh"], stats["expired"]) == (2, 1, 1)
        assert cache.purge_expired() == 1
        assert cache.stats()["entries"] == 1
        cache.delete("fresh")
        assert cache.stats()["entries"] == 0


def test_corrupted_entry_is_discarded(tmp_path):
    with DiskCache(tmp_path / "c.sqlite") as cache:
        cache._conn.execute("INSERT INTO entries VALUES (?, ?, ?)", ("bad", datetime.now(timezone.utc).isoformat(), "{not json"))
        cache._conn.commit()
        assert cache.get("bad") is None
        assert cache.stats()["entries"] == 0
