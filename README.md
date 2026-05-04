## Project: Profiles API (Stage 3)

A FastAPI + SQLModel API for managing and querying `Profile` records stored in SQLite.

It supports:

- Structured filtering via `GET /api/profiles`
- Natural language search via `GET /api/profiles/search?query=...`
- Fetching a single profile via `GET /api/profiles/{profile_id}`

### Tech Stack

- FastAPI
- SQLModel (SQLAlchemy)
- SQLite (local `sqlite.db`)
- UUIDv7 IDs (via `uuid7-standard`)

---

## Setup & Run

### Prerequisites

- Python 3.13+
- `uv` installed

### Install dependencies

From `stage-3/`:

```bash
uv sync
```

### Start the API

From `stage-3/`:

```bash
uv run fastapi dev
```

The configured entrypoint is `app.main:app` (see `pyproject.toml`).

### Database

On startup, the app runs `create_db_and_tables()` (see `app/main.py`) and creates `sqlite.db` if needed.

### Seed data (optional)

If you want to seed the database using `seed.py`, run from `stage-3/` so the JSON path resolves correctly:

```bash
uv run python seed.py
```

---

## API Overview

### 1) List profiles (structured filters)

`GET /api/profiles`

Query params are parsed by `FilterParams` in `app/util.py`:

- `gender`, `age_group`, `country_id`
- `min_age`, `max_age`
- `min_gender_probability`, `min_country_probability`
- Pagination: `page`, `limit`
- Sorting: `sort_by` (`age`, `created_at`, `gender_probability`), `order` (`asc`, `desc`)

### 2) Natural language search (core feature)

`GET /api/profiles/search?query=...`

Example:

```text
/api/profiles/search?query=young%20males%20from%20nigeria
```

---

## Natural Language Parsing Approach

Natural language parsing is implemented in `app/util.py` as `filter_search_profiles()`.

It uses a simple keyword + rule-based parser:

- Lowercases and splits the query into tokens
- Scans tokens and sets filter variables (`gender`, `min_age`, `max_age`, `age_group`, `country_name`)
- Converts those into SQL filters (`WHERE` clauses)
- Applies pagination (`page`, `limit`)

### Supported keywords and mappings

#### Gender keywords

If any of these tokens appear, the parser adds the corresponding gender filter:

- Male keywords: `male`, `males`, `man`, `men`, `guy`, `guys`, `boy`, `boys`, `gentleman`, `gentlemen`
- Female keywords: `female`, `females`, `women`, `woman`, `lady`, `ladies`, `girl`, `girlsgentlewomen`

Mapping:

- One gender found → `Profile.gender == <gender>`
- Both genders found → `Profile.gender IN ('male', 'female')`

#### Age keywords

- `young` → `min_age = 16`, `max_age = 24`
- `above <N>` → `min_age = N`
- `below <N>` → `max_age = N`
- A standalone number token `<N>` (and no min/max set) → `age == N`

#### Age group keywords

- Teenager: `teen`, `teenager`, `teenagers`, `teenage` → `age_group = 'teenager'`
- Adult: `adult`, `adults`, `adulthood` → `age_group = 'adult'`
- Senior: `old`, `elder`, `elderly`, `senior`, `seniors` → `age_group = 'senior'`

#### Country keywords

- `from <word>` or `in <word>` → `country_name = <word>`

Mapping:

- `Profile.country_name ILIKE <country_name>` (case-insensitive match)

### How the logic works (high level)

1. Tokenize: `query.strip().lower().split()`
2. Single pass token scan:
  - Detect gender tokens (can be multiple)
  - Detect `young`, `above`, `below`, numeric ages
  - Detect age group tokens
  - Detect `from`/`in` then take the next token as country
3. Build a `select(Profile)` statement and append `where()` clauses based on detected filters.
4. If nothing was detected → returns `400 Unable to interpret query`
5. If query interpreted but no results → returns `404 No profiles found`

---

## Limitations / Edge Cases

- Only single-token country names: `from united states` won’t work (it only picks the next word after `from/in`).
- No ISO country code mapping: it matches `country_name` directly, not `country_id`, and doesn’t translate “nigeria” → “NG”.
- No synonyms for “above/below”: `over`, `under`, `older than`, `younger than`, `>=` aren’t handled.
- No range parsing: `between 18 and 25` isn’t handled.
- `young` is hard-coded to 16–24 and may conflict with other age terms if both are present.
- Conflicting tokens: the last matching age-group token wins (e.g. “adult teenager”).
- Gender duplication: repeated tokens can add duplicates to the gender list (though `IN (...)` still works).

