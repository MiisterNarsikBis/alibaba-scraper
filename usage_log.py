"""Request log with byte accounting, persisted to disk.

Tracks each call routed through route_call: endpoint, query params, whether it
hit the cache (= no proxy consumed) or went upstream, response status,
duration, and the BYTES that crossed the proxy (residential proxies bill by
volume, not by call count). Used by GET /usage so the client can see what ate
quota.

Bytes come from curl_cffi (`response_size` + `request_size`, i.e. the compressed
bytes on the wire, headers included). They are a LOWER bound: the TLS/CONNECT
handshake of each new connection (~5-7 KB) is not counted, and the one browser
surface (/suppliers/search, a real Chrome) is not metered at all.

Persistence: one JSON line per call in USAGE_LOG_PATH (empty = memory only),
reloaded at startup, rotated at 50 MB. No PII, no token, query params sanitized.
"""
import json
import os
import threading
import time
from collections import deque
from threading import Lock

_MAX_ENTRIES = 1000
_MAX_FILE_BYTES = 50 * 1024 * 1024
_log = deque(maxlen=_MAX_ENTRIES)
_lock = Lock()
_tl = threading.local()          # bytes of the request being served by this worker thread
_days = {}                       # "YYYY-MM-DD" -> counters (whole history)
_endpoints = {}                  # endpoint -> counters (whole history)
_since = None


def _path():
    return os.environ.get("USAGE_LOG_PATH", "")


def _zero():
    return {"calls": 0, "proxy_calls": 0, "errors": 0, "bytes_down": 0, "bytes_up": 0}


def _bump(table, key, e):
    c = table.setdefault(key, _zero())
    c["calls"] += 1
    c["proxy_calls"] += 1 if e["proxy_consumed"] else 0
    c["errors"] += 1 if (e["status"] >= 400 or e.get("error")) else 0
    c["bytes_down"] += e.get("bytes_down", 0)
    c["bytes_up"] += e.get("bytes_up", 0)


def begin():
    """Start of a request: reset this thread's byte counters."""
    _tl.down = _tl.up = _tl.reqs = 0


def add_transfer(down, up):
    """One upstream HTTP exchange through the proxy (called by the metered curl session)."""
    _tl.down = getattr(_tl, "down", 0) + int(down or 0)
    _tl.up = getattr(_tl, "up", 0) + int(up or 0)
    _tl.reqs = getattr(_tl, "reqs", 0) + 1


def _take():
    d, u, n = getattr(_tl, "down", 0), getattr(_tl, "up", 0), getattr(_tl, "reqs", 0)
    _tl.down = _tl.up = _tl.reqs = 0
    return d, u, n


def _append_file(entry):
    p = _path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > _MAX_FILE_BYTES:
            os.replace(p, p + ".1")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # measuring must never break a request


def record(*, endpoint, params, cache_hit, status, duration_ms, error=None):
    """Append one entry to the log (memory + disk)."""
    global _since
    safe_params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    down, up, reqs = _take()
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
        "upstream_requests": reqs,
        "bytes_down": down,
        "bytes_up": up,
    }
    with _lock:
        _log.append(entry)
        _since = _since or entry["iso"]
        _bump(_days, entry["iso"][:10], entry)
        _bump(_endpoints, endpoint, entry)
        _append_file(entry)


def load():
    """Reload the persisted history at startup (ring buffer + whole-history aggregates)."""
    global _since
    p = _path()
    if not p:
        return
    for path in (p + ".1", p):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(e, dict) or "iso" not in e:
                        continue
                    _log.append(e)
                    _since = _since or e["iso"]
                    _bump(_days, e["iso"][:10], e)
                    _bump(_endpoints, e.get("endpoint", "?"), e)
        except OSError:
            pass


def snapshot(limit=100):
    """Return the most recent `limit` entries (newest last)."""
    with _lock:
        entries = list(_log)
    return entries[-limit:]


def stats():
    """Ring-buffer counters (last 1000 calls, kept for compatibility) plus
    `lifetime` counters over the whole persisted history. Use `lifetime` for deltas:
    the ring buffer caps at 1000 entries."""
    with _lock:
        entries = list(_log)
        days = {d: dict(c) for d, c in _days.items()}
        eps = {e: dict(c) for e, c in _endpoints.items()}
        since = _since
    total = _zero()
    for c in days.values():
        for k in total:
            total[k] += c[k]
    total["bytes_total"] = total["bytes_down"] + total["bytes_up"]
    month = time.strftime("%Y-%m", time.gmtime())
    month_bytes = sum(c["bytes_down"] + c["bytes_up"] for d, c in days.items() if d.startswith(month))
    lifetime = {
        "since": since, **total, "month": month, "month_bytes": month_bytes,
        "bytes_by_endpoint": dict(sorted(((e, c["bytes_down"] + c["bytes_up"]) for e, c in eps.items()), key=lambda x: -x[1])),
        "by_day": {d: {**c, "bytes_total": c["bytes_down"] + c["bytes_up"]} for d, c in sorted(days.items())[-31:]},
        "unmetered": ["suppliers/search (real Chrome browser)", "TLS/CONNECT handshake overhead (~5-7 KB per new connection)"],
        "persisted": bool(_path()),
    }
    if not entries:
        return {"total": 0, "proxy_consumed": 0, "cache_hits": 0, "errors": 0, "by_endpoint": {}, "since": None, "lifetime": lifetime}
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
        "total": len(entries), "proxy_consumed": proxy, "cache_hits": hits, "errors": errs,
        "since": entries[0]["iso"], "by_endpoint": dict(sorted(by_ep.items(), key=lambda x: -x[1])),
        "lifetime": lifetime,
    }


def clear():
    """Full reset: memory, aggregates and the persisted file."""
    global _since
    with _lock:
        _log.clear()
        _days.clear()
        _endpoints.clear()
        _since = None
        p = _path()
        for path in (p, p + ".1") if p else ():
            try:
                os.remove(path)
            except OSError:
                pass


load()
