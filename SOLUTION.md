# Stage 4B — System optimization & CSV ingestion

This document describes what was implemented for **query performance**, **deterministic query normalization / caching**, and **large CSV ingestion**, using **PostgreSQL** as the primary database.

## 1. Query performance and database efficiency

### Approach

- **PostgreSQL + connection pooling** — The app uses a single SQLAlchemy engine with `pool_pre_ping=True`, configurable `pool_size` and `max_overflow` (see `Settings` in [`app/config.py`](app/config.py)). This cuts per-request connection setup cost compared to opening a new TCP session for every query, which matters when the database is remote.
- **Filtered `COUNT` for `GET /api/profiles`** — List pagination `total` now comes from `COUNT(*)` over the **same** filtered `SELECT` as the page rows (subquery), instead of counting the whole table. Results stay in the same JSON shape; correctness for filtered pages improves.
- **Single-pass search** — `GET /api/profiles/search` computes `COUNT` once and fetches the current **page** once. Previously the page query could run twice.
- **Indexes** — Composite / helper btree indexes on `Profile` align with common filters and sort keys: `(gender, age)`, `(country_id, age)`, `age_group`, `created_at`, and `country_name` (see [`app/model/profiles.py`](app/model/profiles.py)). This reduces sequential scans as the table grows.

### Trade-offs

- **Exact filtered counts** — `COUNT` over a large filtered set is still O(result set) work; indexes reduce scan cost but do not make counting free. Keeping the API contract (exact `total` + `total_pages`) was prioritized over approximate counts.
- **Operational complexity** — Moving from SQLite to PostgreSQL adds backups, credentials, and connection limits. [`docker-compose.yml`](docker-compose.yml) documents a minimal local setup; production should use a managed instance.

### Before / after (latency)

Measurements depend on hardware, network, and dataset size. The table below is **illustrative** for “same app code path, remote Postgres, ~1M-row table with warm cache / OS cache,” using a typical filtered list or search:

| Scenario | Before (baseline assumptions) | After (expected direction) |
|----------|--------------------------------|----------------------------|
| `GET /api/profiles` with `gender` + age range (cold cache, no indexes) | High p95: full table scan + wrong global count | Lower p95: index-friendly filters + correct filtered count |
| Same request repeated within TTL | Same cost each time | **Cache hit**: mostly in-process work, sub-ms to low ms |
| `GET /api/profiles/search` | Extra round-trip from double page fetch | One page fetch + one count |
| Under concurrent imports | N/A (SQLite not used) | Readers less blocked; writers batched with chunk commits |

Run your own **before/after** with `ab`, `httpx`, or similar against your environment and paste numbers here when submitting.

---

## 2. Query normalization and cache efficiency

### Approach

- **Canonical cache keys** — Structured [`FilterParams`](app/util.py) are normalized (e.g. gender lowercased, `country_id` uppercased, `age_group` lowercased, `min_age`/`max_age` swapped if inverted) and serialized with **sorted keys** and stable JSON (`sort_keys=True`).
- **Search** — The existing **rule-based** parser (no LLMs) is refactored into `parse_search_query()` → [`ParsedSearchFilters`](app/util.py). Genders are **sorted unique** tuples; country token contributes a **casefolded** field to the cache key so phrases that map to the same filters share a key.
- **In-process TTL cache** — [`cachetools.TTLCache`](app/util.py) stores frozen list/search responses (`count` + `Profile` rows as JSON-safe dicts, rehydrated with `Profile.model_validate`). TTL and max entries are configurable via `Settings`.
- **Invalidation** — Cache is cleared after **successful** single-row create, **delete**, and **CSV import** so admins do not see stale lists for long (`invalidate_profiles_cache()`).

### Trade-offs

- **Process-local cache** — Not shared across multiple API workers. With “no horizontal scaling” in the brief, one process (or accept best-effort per worker) is acceptable; use a short TTL.
- **Semantics unchanged** — Normalization applies only to values the parser / query params already express; we do not infer new NL meanings.

---

## 3. CSV data ingestion

### Approach

- **Endpoint** — `POST /api/profiles/import` (multipart file), **admin-only**, same family as other writes. Response shape matches the brief (`status`, `total_rows`, `inserted`, `skipped`, `reasons`).
- **Streaming** — The upload is read through a **binary** file handle wrapped in `io.TextIOWrapper`; the CSV is consumed row-by-row via [`csv.reader`](app/ingest_csv.py). The server does **not** load the full CSV into a single string or list of all rows.
- **Batched commits** — Valid rows are accumulated and flushed in chunks (`csv_import_batch_size`, default 1000). Each flush runs **`session.commit()`** for that batch. A failure **after** some batches still leaves earlier batches committed (no whole-upload rollback).
- **Heavy work off the event loop** — Import runs in `asyncio.to_thread` with a **fresh** `Session` bound to the global engine so the async loop can keep handling other HTTP traffic.
- **Skips and reasons** — Rows are skipped with counters for: missing fields, invalid age / gender / age_group / probability, malformed row length, duplicate `name` (DB or within-file), and replacement-character heuristic for encoding issues. Only non-zero reason counts appear in `reasons`.

### Failure and edge-case handling

- **Bad header** — If the header row is not exactly the expected column list, the API returns **400** with a clear message (fail fast before scanning many rows).
- **Per-row errors** — Never abort the whole file; increment `skipped` and the appropriate `reasons` bucket.
- **Mid-import DB errors** — A failed `commit` loses only the **current** batch; prior batches remain. Operators can re-run with idempotent skip behavior on duplicate names.
- **Concurrent uploads** — PostgreSQL handles concurrent sessions better than SQLite; connection pool exhaustion remains a risk if many huge imports run at once—tune pool size and `max_connections`.

---

## 4. Running locally

1. Start Postgres, e.g. `docker compose up -d postgres`.
2. Set `DATABASE_URL` in `.env` (see [`.env.example`](.env.example)).
3. `uv sync` then `uv run fastapi dev` — tables are created on startup (`create_db_and_tables`).

---

## 5. Files touched (reference)

| Area | Files |
|------|--------|
| DB / config | [`app/database.py`](app/database.py), [`app/config.py`](app/config.py), [`docker-compose.yml`](docker-compose.yml), [`.env.example`](.env.example), [`pyproject.toml`](pyproject.toml) |
| Model / indexes | [`app/model/profiles.py`](app/model/profiles.py) |
| Queries / cache / NL parse | [`app/util.py`](app/util.py) |
| Import | [`app/ingest_csv.py`](app/ingest_csv.py), [`app/routers/profiles.py`](app/routers/profiles.py) |
