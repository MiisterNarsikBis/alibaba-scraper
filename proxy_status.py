"""Proxy and service status for monitoring (GET /proxy/status).

Answers three questions without calling Alibaba: is the service healthy, how much
residential-proxy traffic did we use, and is the balance getting low. A dashboard
(the Radar) reads this JSON; nothing is pushed anywhere.

Traffic sources, best first:
  1. Decodo's API (DECODO_API_KEY): traffic used this month as the provider bills it.
     Documented endpoint: GET https://api.decodo.com/v2/sub-users/{id}/traffic?type=month
     (the response carries `traffic`, `traffic_rx`, `traffic_tx`; the unit is NOT stated
     in the docs, so we compare it with our own metered bytes and report both).
  2. Our own metering (usage_log): a LOWER bound of what the proxy bills.

Environment (all optional):
  DECODO_API_KEY         API key created in the Decodo dashboard (not the proxy login)
  DECODO_SUB_USER_ID     sub-user whose traffic to read (default: the first one returned)
  PROXY_PLAN_GB          size of the plan / budget for the month, in GB
  PROXY_WARN_PCT         warn when less than this % is left (default 20)
  PROXY_CRIT_PCT         critical when less than this % is left (default 5)
"""
import json
import os
import time
import urllib.error
import urllib.request

import usage_log

_API = "https://api.decodo.com"
_CACHE_S = 600
_cache = {"at": 0.0, "value": None}
GB = 1024 ** 3  # reported as GiB; the Decodo unit is checked against our own bytes in `decodo.ratio_vs_measured`


def _decodo():
    """Traffic used this month according to Decodo, or None when no key / on failure (never raises)."""
    key = os.environ.get("DECODO_API_KEY", "")
    if not key:
        return None
    if _cache["value"] is not None and time.time() - _cache["at"] < _CACHE_S:
        return _cache["value"]

    def get(path):
        # Documented format: `Authorization: <api key>` + `accept: application/json`. An explicit User-Agent is needed:
        # urllib's default one is commonly refused (403) by API gateways.
        req = urllib.request.Request(_API + path, headers={"Authorization": key, "accept": "application/json", "User-Agent": "alibaba-scraper-proxy-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    out = {"ok": False}
    try:
        sub = os.environ.get("DECODO_SUB_USER_ID", "")
        if not sub:
            users = get("/v2/sub-users")
            rows = users if isinstance(users, list) else (users.get("data") or users.get("sub_users") or [])
            sub = str((rows[0].get("id") if rows and isinstance(rows[0], dict) else "") or "")
        if not sub:
            raise ValueError("no sub-user found")
        t = get("/v2/sub-users/%s/traffic?type=month&service_type=residential_proxies" % sub)
        out = {"ok": True, "sub_user": sub, "traffic": t.get("traffic"), "traffic_rx": t.get("traffic_rx"), "traffic_tx": t.get("traffic_tx"), "raw_keys": sorted(t)}
    except urllib.error.HTTPError as e:
        out = {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:  # monitoring must never break the service
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    _cache.update(at=time.time(), value=out)
    return out


def _last_24h():
    since = time.time() - 24 * 3600
    ent = [e for e in usage_log.snapshot(1000) if e.get("timestamp", 0) >= since]
    errs = [e for e in ent if e.get("status", 0) >= 400 or e.get("error")]
    blocked = [e for e in errs if "block" in str(e.get("error", "")).lower() or "punish" in str(e.get("error", "")).lower()]
    hour = [e for e in blocked if e.get("timestamp", 0) >= time.time() - 3600]
    proxy_calls = [e for e in ent if e.get("proxy_consumed")]
    return {"calls": len(ent), "proxy_calls": len(proxy_calls), "errors": len(errs), "blocked": len(blocked), "blocked_last_hour": len(hour),
            "error_rate": round(len(errs) / len(ent), 3) if ent else 0.0}


def status():
    life = usage_log.stats()["lifetime"]
    measured_gb = life["month_bytes"] / GB
    plan = float(os.environ.get("PROXY_PLAN_GB", "0") or 0)
    warn, crit = float(os.environ.get("PROXY_WARN_PCT", "20")), float(os.environ.get("PROXY_CRIT_PCT", "5"))
    dec = _decodo()
    traffic = {"month": life["month"], "measured_gb": round(measured_gb, 4), "measured_is_lower_bound": True, "plan_gb": plan or None,
               "source": "measured", "used_gb": round(measured_gb, 4)}
    if dec and dec.get("ok") and isinstance(dec.get("traffic"), (int, float)):
        traffic.update(source="decodo", used_gb=dec["traffic"], decodo_reported=dec["traffic"])
        if measured_gb > 0:
            traffic["decodo_vs_measured"] = round(dec["traffic"] / measured_gb, 2)  # ~1 = same unit (GiB); ~1.07 = GB; >1.3 = overhead/other traffic
    if plan:
        traffic["remaining_gb"] = round(max(plan - traffic["used_gb"], 0), 4)
        traffic["remaining_pct"] = round(100 * traffic["remaining_gb"] / plan, 1)
    d24 = _last_24h()
    reasons, level = [], "ok"

    def raise_to(l, why):
        nonlocal level
        reasons.append(why)
        if l == "critical" or level == "ok":
            level = l
    if "remaining_pct" in traffic:
        if traffic["remaining_pct"] <= crit:
            raise_to("critical", "proxy : %.1f %% restant (seuil %.0f %%)" % (traffic["remaining_pct"], crit))
        elif traffic["remaining_pct"] <= warn:
            raise_to("warn", "proxy : %.1f %% restant (seuil %.0f %%)" % (traffic["remaining_pct"], warn))
    if d24["calls"] >= 10 and d24["error_rate"] >= 0.3:
        raise_to("warn", "%d %% d'erreurs sur 24 h (%d appels)" % (round(100 * d24["error_rate"]), d24["calls"]))
    if d24["blocked_last_hour"] >= 5:
        raise_to("critical", "Alibaba bloque : %d refus sur la dernière heure" % d24["blocked_last_hour"])
    if dec is not None and not dec.get("ok"):
        raise_to("warn", "API Decodo injoignable (%s) : solde non vérifié" % dec.get("error"))
    if not plan and dec is None:
        reasons.append("solde non surveillé : renseigner PROXY_PLAN_GB (et DECODO_API_KEY)")
    return {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "level": level, "reasons": reasons, "traffic": traffic,
            "last_24h": d24, "decodo": dec, "balance_monitored": bool(plan and (dec and dec.get("ok")))}
