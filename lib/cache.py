"""Redis client + JSON cache helpers + per-route in-memory caches.

Single module owning every "is this in Redis?" question. Imports from
`lib.config` for connection settings. Used by every API blueprint that
needs to write to or read from the bridged Redis keys
(`features:<file>`, `bkafi:flat`, `bkafi:by_file`, …).

In-memory caches (`_features_cache`, `_bkafi_cache`, `_bkafi_by_file_cache`)
are module-level dicts that mirror the Redis state. They avoid a Redis
round-trip + json.loads on every per-building request — significant on the
demo's hot paths (e.g. `/api/buildings/status` reads bkafi for ~470 cands).

Designed to be safe to call before Redis is reachable: every helper
returns `None`/`False` if the connection isn't available rather than
raising.
"""
import json
from typing import Optional

import redis

from lib.config import REDIS_URL, CACHE_TTL_SECONDS


# ---------------------------------------------------------------------------
# Redis client (lazy singleton)
# ---------------------------------------------------------------------------

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """Return a cached, connected Redis client, or `None` if the server isn't
    reachable. The first successful call pings the server; subsequent calls
    return the same client instance. Failures are silent — callers should
    treat `None` as "no Redis available right now" and continue without it."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception:
        return None


def cache_get_json(key: str):
    """Read a JSON-encoded value from Redis. Returns the decoded object or
    `None` (key missing OR Redis unavailable OR decode failure)."""
    client = get_redis_client()
    if not client:
        return None
    raw = client.get(key)
    if not raw:
        return None
    return json.loads(raw)


def cache_set_json(key: str, payload, ttl: int = CACHE_TTL_SECONDS) -> bool:
    """Write `payload` to Redis at `key` with the given TTL. Returns `True`
    on success, `False` if Redis is unreachable. Errors propagate from
    `client.set` (the caller's responsibility — usually a worker task that
    should crash on a real Redis failure)."""
    client = get_redis_client()
    if not client:
        return False
    client.set(key, json.dumps(payload), ex=ttl)
    return True


# ---------------------------------------------------------------------------
# In-memory mirror of Redis keys (read-through cache)
# ---------------------------------------------------------------------------

# `features_cache` is kept as a public mutable dict for the legacy
# /api/features/calculate path (single-process workflow — the dict is
# only written from the same web process that reads it). Once that path
# is deleted, this can go too.
features_cache: dict = {}              # {file_path: {building_id: {feature: value}}}


def get_features_cache(file_path: str) -> Optional[dict]:
    """Return the cached feature dict for `file_path`, or `None` if neither
    the in-memory nor Redis-backed copy is available. Warms the in-memory
    cache when Redis is the source of truth."""
    if file_path in features_cache:
        return features_cache[file_path]
    cached = cache_get_json(f'features:{file_path}')
    if cached is not None:
        features_cache[file_path] = cached
        return cached
    return None


# bkafi is written by the Celery WORKER process (via _bridge_to_legacy after
# each stage) and read by the WEB process. An in-memory mirror on the web
# side would go stale the moment the worker writes new data to Redis — the
# Step 2 → Step 3 transition is the canonical trigger (Step 2 writes
# stub predicted_label=0 pairs, Step 3 writes real classifier scores).
# Every request reads from Redis fresh. Sub-ms cost; correctness > caching.


def get_bkafi_cache() -> Optional[dict]:
    """Return the bkafi:flat dict, always fresh from Redis."""
    return cache_get_json('bkafi:flat')


def set_bkafi_cache(payload: Optional[dict]) -> None:
    """No-op shim. The in-memory bkafi cache was removed (it served stale
    Step 2 stubs after Step 3 wrote real scores to Redis). Callers in the
    worker still wrap their cache_set_json('bkafi:flat', ...) write with
    this, which is now redundant but harmless."""
    return


def get_bkafi_by_file_cache() -> Optional[dict]:
    """Return the bkafi:by_file dict, always fresh from Redis."""
    return cache_get_json('bkafi:by_file')


def set_bkafi_by_file_cache(payload: Optional[dict]) -> None:
    """No-op shim — see set_bkafi_cache."""
    return


def invalidate_buildings_status_cache(file_path: Optional[str] = None) -> None:
    """No-op shim. The per-file in-process buildings-status cache was removed
    (see plan addendum 9) — readers always recompute from Redis now. Kept as
    a callable so the legacy routes still in app.py don't AttributeError.

    If `file_path` is None, callers expect "invalidate everything"; if a
    path is passed, "invalidate just that file's cache". Both are no-ops."""
    return None
