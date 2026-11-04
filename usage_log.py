"""Lightweight in-memory request log.

Tracks each call routed through route_call: endpoint, query params, whether it
hit the cache (= no proxy consumed) or went upstream (= one residential
proxy request), response status, duration. Used by GET /usage so Pépite can
see what ate quota.

1000-entry ring buffer. No persistence, no PII, no token shown — query
params are sanitized so callers can introspect usage without leaking their
filter values into log readers.
"""
import time
from collections import deque
from threading import Lock

_MAX_ENTRIES = 1000
_log = deque(maxlen=_MAX_ENTRIES)
_lock = Lock()


def record(*, endpoint, params, cache_hit, status, duration_ms, error=None):
    """Append one entry to the in-memory log."""
    safe_params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    entry = {
        "timestamp": int(time.time()),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": endpoint,
        "params": safe_params,
        "cache_hit": bool(cache_hit),
        "proxy_consumed": not bool(cache_hit),
        "status": int(status),
        "duration_ms": round(duration_ms, 1),
        "error": error,
    }
    with _lock:
        _log.append(entry)


def snapshot(limit=100):
    """Return the most recent `limit` entries (newest last)."""
    with _lock:
        entries = list(_log)
    return entries[-limit:]


def stats():
    """Aggregate counters over the ring buffer."""
    with _lock:
        entries = list(_log)
    if not entries:
        return {"total": 0, "proxy_consumed": 0, "cache_hits": 0,
                "errors": 0, "by_endpoint": {}, "since": None}
    by_ep = {}
    proxy = hits = errs = 0
    for e in entries:
        by_ep[e["endpoint"]] = by_ep.get(e["endpoint"], 0) + 1
        if e["proxy_consumed"]:
            proxy += 1
        else:
            hits += 1
        if e["status"] >= 400 or e.get("error"):
            errs += 1
    return {
        "total": len(entries),
        "proxy_consumed": proxy,
        "cache_hits": hits,
        "errors": errs,
        "since": entries[0]["iso"],
        "by_endpoint": dict(sorted(by_ep.items(), key=lambda x: -x[1])),
    }


def clear():
    with _lock:
        _log.clear()
