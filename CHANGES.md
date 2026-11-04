# Custom modifications — do not lose these when pulling upstream

Branch `master` = upstream (omkarcloud/alibaba-scraper).
Branch `custom` = our local adaptations, rebased on top of master.

## Files modified

### `alibaba/parsers.py`
- **Added** `_parse_main_markets(raw)` helper (around line ~860, before
  `parse_store_home`). Handles CSV, JSON-array, JSON-string, list inputs.
  Returns `[{"market": str, "percent": float|None}, ...]`. Defensive
  against malformed JSON — returns `[]` instead of crashing.
- **`parse_store_home` (line ~1270)** — `main_markets` extraction now reads
  multiple field names (`companyMainMarket`, `companyMainMarkets`,
  `mainMarkets`, `companyMarketList`, nested `companyMainMarket.value`).

### `alibaba/refs.py`
- **`resolve_supplier_ref`** — relaxed the `value.isdigit()` check from
  "reject any all-digit value" to "reject all-digit values > 7 chars".
  Numeric subdomains (e.g. `51899`) are now accepted; long numeric
  companyIds are still rejected.

### `cache.py`
- **Added** module-level `_last_cache_hit` flag and `was_cache_hit()` helper.
  `cached_call` sets `_last_cache_hit = True` when serving from cache.

### `route_glue.py`
- **`route_call`** now instruments every request via `usage_log.record()`:
  captures endpoint, params, cache_hit, status, duration_ms, error.
  Wraps all 7 exception branches (400/404/502/500) plus the success path.

### `routes.py`
- **Added** 3 admin routes (registered under both `/...` and `/alibaba/...`):
  - `GET /usage` — aggregate stats (total / proxy_consumed / cache_hits / errors / by_endpoint)
  - `GET /usage/recent?limit=N` — last N entries (default 100, max 1000)
  - `GET /usage/clear` — reset the in-memory log
- Imports `request` from bottle (was missing) and `usage_log`.

### `docker-compose.yaml`
- Port mapping: `127.0.0.1:8000:8000` (was `8000:8000`).
- Environment: `ALIBABA_PROXY=${ALIBABA_PROXY:-}` (uncommented) and
  `ALIBABA_CACHE=${ALIBABA_CACHE:-0}` (added).

## Files added

- **`usage_log.py`** — in-memory ring buffer (1000 entries) for the usage log.
- **`alibaba.kodly.fr.conf.example`** — template of the Apache vhost for
  `https://alibaba.kodly.fr` with `X-API-Key` auth. Copy it to
  `/etc/apache2/sites-available/alibaba.kodly.fr.conf`, replace
  `__API_TOKEN__` with the real key, then reload Apache. The real file holds
  the token in clear: it is git-ignored, keep it `chmod 600`, never commit it.
- **`update.sh`** — pulls upstream and rebases `custom` on top. It asks before
  rebuilding the container (use `--yes` to skip the question).

## Files ignored (do not commit)

- `.api-token` — the real X-API-Key. Generated with `openssl rand -hex 32`,
  stored here only for reference, chmod 600.
- `.env` — proxy credentials. Lives at the repo root.
- `alibaba.kodly.fr.conf` — the real vhost (contains the API token in clear).

Rotating the API key means changing it in three places: `.api-token`, the
vhost (`SetEnvIf X-API-Key ...`, then reload Apache) and every client's config.

## Update workflow

1. `git checkout master && git pull` — bring in upstream changes.
2. `git checkout custom && git rebase master` — replay our changes on top.
3. Resolve conflicts (if any). All our changes are described above so they
   are easy to spot.
4. `docker compose up -d --build` — rebuild with the merged code.
5. Run `update.sh` (next to this file) to do steps 1-3 in one shot, with
   a dry-run flag (`--check`) for inspection. It asks before step 4.

## What is NOT modified

- All upstream logic that we don't need remains untouched.
- `botasaurus` settings, image search, cache TTLs, schemas, fetcher — all
  inherited as-is.
