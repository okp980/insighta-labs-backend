# Insighta Labs+ — Backend API

Secure demographic intelligence API: **profiles** with structured filters, **rule-based natural-language search**, **GitHub OAuth** (browser + **PKCE** for CLI-style clients), **JWT** access/refresh handling, **RBAC**, **CSV export**, and **batched CSV import**. Built with **FastAPI**, **SQLModel**, and **PostgreSQL** (set `DATABASE_URL`; local dev: [`docker-compose.yml`](docker-compose.yml)).

**This repository is the backend only.** A separate CLI (Node or other) and a web portal can integrate against the same auth and data model. Three repos, one API contract.

## System architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Insighta Labs+                           │
│                                                             │
│   ┌──────────────┐         ┌─────────────────────────────┐  │
│   │  CLI / native│         │  Web portal (any SPA stack)   │  │
│   │  (optional)  │         │  HttpOnly cookies + CSRF      │  │
│   └──────┬───────┘         └──────────────┬──────────────┘  │
│          │ PKCE + JSON tokens            │ Cookie session   │
│          │ (send access_token as Cookie  │ to API           │
│          │  on `/api/*` in current code) │                  │
│          └────────────────┬─────────────┘                  │
│                             ▼                                │
│              ┌──────────────────────────────┐                │
│              │   Backend API (this repo)    │                │
│              │   FastAPI + SQLModel + PostgreSQL│                │
│              │   JWT + OAuth + RBAC         │                │
│              └──────────────────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

All interfaces share the same users, refresh-token store, and profile data served by this service.

## Quick start

```bash
uv sync
cp .env.example .env
# Fill in GitHub OAuth credentials, SECRET, and URLs

uv run fastapi dev
```

Production-style run (after configuring a production ASGI server):

```bash
uv run fastapi run
```

Optional — seed the database (from repo root):

```bash
uv run python seed.py
```

Requires **Python 3.13+** and **`uv`**. App entrypoint: `app.main:app` (`pyproject.toml`).

## Environment variables

| Variable | Description |
| -------- | ----------- |
| `GITHUB_CLIENT_ID` | GitHub OAuth App client ID (browser flow) |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App client secret |
| `GITHUB_REDIRECT_URI` | Must match GitHub callback URL (e.g. `http://127.0.0.1:8000/auth/github/callback`) |
| `SECRET` | JWT signing key and Starlette **session** secret (OAuth state); use a long random value in production |
| `DATABASE_URL` | SQLAlchemy URL (e.g. `postgresql+psycopg://user:pass@host:5432/dbname`) |
| `FRONTEND_URL` | SPA origin used after OAuth redirect |
| `CORS_ALLOW_ORIGINS` | Comma-separated allowed origins |
| `COOKIE_SECURE` | Set `true` behind HTTPS |
| `COOKIE_SAMESITE` | Cookie `SameSite` (default `lax`) |
| `GITHUB_CLI_CLIENT_ID` | Optional: OAuth app for CLI/native PKCE |
| `GITHUB_CLI_CLIENT_SECRET` | Optional: secret for CLI token exchange |
| `GITHUB_CLI_REDIRECT_URI` | Optional: loopback callback (default `http://127.0.0.1:42069/callback`) |

See `.env.example` for a ready-to-copy template.

## Authentication flow

### GitHub OAuth App setup

1. GitHub → **Settings** → **Developer settings** → **OAuth Apps** → **New OAuth App**
2. **Homepage URL**: your app or API URL as appropriate.
3. **Authorization callback URL**: must equal `GITHUB_REDIRECT_URI` (e.g. `https://<api-host>/auth/github/callback` in production).

### Web flow (browser)

1. User opens the portal login; portal redirects to **`GET /auth/github/login`** (or user hits that URL).
2. **Authlib** redirects to GitHub (PKCE `S256`, scope `user:email`). OAuth **state** is handled via **signed session cookie** (Starlette `SessionMiddleware`), not a custom DB table.
3. User signs in at GitHub.
4. GitHub redirects to **`GET /auth/github/callback?code=...`**
5. API exchanges `code`, loads GitHub user + primary email when available, **upserts** `User` by `github_id`.
6. API issues **access** + **refresh** JWTs; sets **`httpOnly`** cookies (`access_token`, `refresh_token`).
7. API redirects to **`FRONTEND_URL`** (e.g. `/auth/callback` on the SPA).

### CLI flow (PKCE)

Designed for native/CLI clients (`app/routers/auth.py`):

1. **`GET /auth/cli/start`** — returns GitHub **`client_id`** (CLI OAuth app) and **`redirect_uri`**; requires `GITHUB_CLI_CLIENT_ID` on the server.
2. CLI generates **`code_verifier`** / **`code_challenge`** and **`state`**, opens the browser to GitHub’s authorize URL (CLI implements the authorize URL; the server documents `client_id` + `redirect_uri`).
3. User authenticates; GitHub redirects to the CLI’s **local listener** (e.g. `http://127.0.0.1:42069/callback?code=...&state=...`).
4. CLI **`POST /auth/cli/exchange`** with `{ "code", "code_verifier", "state" }`.
5. Server exchanges the code with GitHub (with `GITHUB_CLI_CLIENT_SECRET`), upserts the user, returns **`{ access_token, refresh_token, user }`** JSON (**no** auth cookies for this path).

**Note:** `get_current_user` reads the **`access_token` cookie** only. CLI integrations calling **`/api/*`** should send that token as a **`Cookie: access_token=...`** header (or extend the API to accept `Authorization: Bearer`). Refresh via **`POST /auth/refresh`** currently expects the **`refresh_token` cookie** plus CSRF header when using cookies.

## Token handling

| Token | TTL (defaults in code) | Persistence | Typical transport |
| ----- | ---------------------- | ----------- | ------------------- |
| Access | ~3 min (`ACCESS_TOKEN_MINUTES`) | Stateless JWT | `access_token` **HttpOnly** cookie |
| Refresh | ~5 min (`REFRESH_TOKEN_MINUTES`) | **PostgreSQL** `RefreshToken` row (`token`, `expires_at`, `is_revoked`) | `refresh_token` **HttpOnly** cookie (web) or JSON (CLI exchange) |

- **Rotation:** `POST /auth/refresh` decodes the refresh JWT, **revokes** the existing DB row, mints a **new** pair, persists a new refresh row.
- **Logout:** `POST /auth/logout` revokes the refresh row and clears cookies.
- **Expiry:** JWT `exp` is enforced on verify; expired refresh → **401**. There is **no** MongoDB-style TTL index; cleanup of old rows is not implemented in application code beyond revocation flags.
- **Cookies:** `httpOnly: true`, `sameSite` and `secure` from **`COOKIE_SECURE`** / **`COOKIE_SAMESITE`** (`app/config.py`).

**Refresh pattern:** short-lived access tokens yield **401** when expired. Clients should call **`POST /auth/refresh`** (with CSRF + cookie rules below), then retry. This is usually implemented in the **SPA or CLI HTTP layer**; FastAPI does not run automatic “silent refresh” middleware on every request.

## Role enforcement

### Roles

| Role | Default for new users | Typical use |
| ---- | --------------------- | ----------- |
| **analyst** | Yes | List, get-by-id, search, **export** |
| **admin** | No | Above + **create** and **delete** |

### How it works

1. **`X-API-Version`** — Required header on all **`/api/profiles`** routes (`verify_api_version`).
2. **`get_current_user`** — Reads **`access_token` cookie**, verifies JWT, loads **`User`**.
3. **`require_roles(...)`** — After authentication, allows only listed roles; otherwise **403** `"Not enough permissions"`.

There is **no** `Authorization: Bearer` branch in current `get_current_user`; role checks apply after a valid access cookie is resolved.

The User model includes **`is_active`** (returned on **`GET /auth/me`**), but **inactive users are not rejected** by middleware in the current codebase—add a check if you need that behavior.

### Role matrix (profiles)

| Endpoint | Analyst | Admin |
| -------- | ------- | ----- |
| `GET /api/profiles` | ✅ | ✅ |
| `GET /api/profiles/{id}` | ✅ | ✅ |
| `GET /api/profiles/search` | ✅ | ✅ |
| `GET /api/profiles/export` | ✅ | ✅ |
| `POST /api/profiles` | ❌ | ✅ |
| `POST /api/profiles/import` | ❌ | ✅ |
| `DELETE /api/profiles/{id}` | ❌ | ✅ |

## API reference

### Auth

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/auth/github/login` | Start GitHub OAuth (redirect) |
| `GET` | `/auth/github/callback` | OAuth callback; sets cookies |
| `POST` | `/auth/refresh` | Rotate tokens (cookies + JSON body) |
| `POST` | `/auth/logout` | Revoke refresh; clear cookies |
| `GET` | `/auth/me` | Current user |
| `GET` | `/auth/cli/start` | CLI: OAuth client metadata |
| `POST` | `/auth/cli/exchange` | CLI: code + PKCE → JSON tokens |

### Profiles

All require **`X-API-Version`** (any agreed value, e.g. `1`) **and** auth unless noted.

| Method | Path | Role | Description |
| ------ | ---- | ---- | ----------- |
| `GET` | `/api/profiles` | analyst, admin | List + filters / sort / pagination |
| `GET` | `/api/profiles/search` | analyst, admin | NL search (`q=...`) |
| `GET` | `/api/profiles/export` | analyst, admin | CSV stream |
| `GET` | `/api/profiles/{id}` | analyst, admin | One profile |
| `POST` | `/api/profiles` | admin | Create (enrichment via external name APIs) |
| `POST` | `/api/profiles/import` | admin | CSV upload, batched insert + skip summary |
| `DELETE` | `/api/profiles/{id}` | admin | Delete |

`GET /` — simple health-style payload.

## Natural language parsing

`GET /api/profiles/search?q=...` uses a **keyword / rule-based** parser in `app/util.py` (`filter_search_profiles`) — **no LLM**.

**Flow (high level)**

1. Lowercase, split on whitespace.
2. Match **gender** token sets (male / female synonyms).
3. Match **age group** keywords → `teenager`, `adult`, `senior`.
4. **`young`** → `min_age=16`, `max_age=24`.
5. **`above N` / `below N`** (literal words `above` / `below` + next token as integer).
6. Standalone digit → exact **age**.
7. **`from` / `in`** + next token → `country_name` (**single-token** country fragment, `ILIKE`).

If nothing matches but the query was non-empty → **400** `Unable to interpret query`. No rows → **404** `No profiles found`.

**Supported keywords (illustrative)**

- **Gender:** e.g. male, men, female, woman, girl, … (see code lists).
- **Age groups:** teen/teenager, adult, old/elder/senior, etc.
- **Special:** `young` (16–24).
- **Age:** `above N`, `below N`, bare number.
- **Country:** `from nigeria`, `in nigeria` (next word only).

**Limitations**

- No negation (“not from nigeria”).
- No OR across multiple countries.
- No fuzzy typo handling.
- No word-form numbers (“thirty”).
- **`above` / `below` only** — not `over` / `under` / “older than” (unless you extend the parser).
- **No** `between X and Y` in current code.
- Country phrase is **one token** after `from`/`in` (e.g. “united states” won’t parse as two words).

## Rate limiting

| Scope | Limit |
| ----- | ----- |
| Selected **`/auth/*`** routes (login, callback, refresh, logout, CLI) | **10 / minute** per client IP (`@limiter.limit` in `app/routers/auth.py`) |
| Default (including **`/api/*`**) | **60 / minute**; key is **`user:<sub>`** from access cookie when valid, else IP (`app/rate_limit.py`) |

Returns **429** with JSON `{ "status": "error", "message": "Too Many Requests" }` when exceeded.

## Request logging

`app/main.py` access log line format:

`METHOD /path -> STATUS (elapsed ms)`

Example:

```text
GET /api/profiles -> 200 (12.34ms)
```

User id is **not** included in the default access log line (add structured logging if you need it).

## Deployment

This service is a standard **ASGI** app, not the Express + `vercel.json` layout from a Node template. Typical approaches:

- Run **`uvicorn`** / **`fastapi run`** behind **nginx** or a cloud load balancer with **HTTPS** and `COOKIE_SECURE=true`.
- Set **all** environment variables in your host’s secret store.
- Point **`GITHUB_REDIRECT_URI`** and **`CORS_ALLOW_ORIGINS`** / **`FRONTEND_URL`** at production URLs.
- Keep **`SECRET`** and GitHub secrets out of version control (use `.env` locally; platform env in prod).

## Security (summary)

- **HttpOnly** JWT cookies for browser flows; **CSRF** double-submit on mutating **`/api/*`** and **`/auth/*`** except OAuth redirect endpoints and **`/auth/cli/*`** (`app/csrf.py`).
- **PKCE** for GitHub OAuth (browser via Authlib; CLI via your client + `/auth/cli/exchange`).
- **RBAC** on profile mutations vs reads/export as in the matrix above.
