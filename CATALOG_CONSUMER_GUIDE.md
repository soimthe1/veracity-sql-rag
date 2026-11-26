# Consuming the catalog

This is for anything that wants to *use* the catalog — most obviously an
AI application that needs to understand a SQL Server schema to write
queries against it, but equally a dashboard, a data-discovery tool, or a
human exploring the [status UI](README.md#web-ui). It documents the REST
contract in `api/schemas.py` and, more importantly, the pattern for using
it safely: **trust status decides what's safe to act on automatically;
confidence is a number, status is the decision.**

Base URL is whatever `catalog.web.api_base_url` / your deployment says —
`http://localhost:8100` in local dev. Every response is JSON. See
`api/schemas.py` for the exact, versioned field list — this guide
explains *how* to use them, not a field-by-field restatement.

## Start here: `POST /v1/resolve`

If you're building an AI application and want the single easiest way in,
use this endpoint. Send a business question or a rough description of
what you're looking for:

```
POST /v1/resolve
{"query": "employee assignments", "limit": 10}
```

You get back, in one call:

- `candidate_entities` — matching tables/views, ranked, each with its
  full semantic info (if any) and **`status`** (see below)
- `business_concepts` — domains/semantic names pulled from the matches,
  useful as a quick sanity check that you found the right area of the
  schema
- `trusted_relationships` — only relationships at `TRUSTED` status
  touching those candidates
- `join_paths` — pre-walked paths (up to 3 hops) between the candidates,
  using only `TRUSTED` relationships
- `notes` — a plain-English caveat about how many candidates aren't yet
  `TRUSTED`

This is deliberately a **read-only, single-call, no-schema-knowledge-
required** entry point — you should not need to inspect SQL Server
metadata yourself, walk `sys.foreign_keys`, or guess at column names to
use it.

## The trust-status pattern (read this before writing anything that
## acts autonomously on catalog data)

Every entity, field, and relationship has both a `confidence` (0.0–1.0
float) and a `status` (one of the values below). **Always branch on
`status`, never on a raw confidence threshold you pick yourself** — the
thresholds and the SUSPECT-regression logic are centralized in
`engine/confidence.py` and tuned against the spec's worked examples (see
[DECISIONS.md](DECISIONS.md)); duplicating that threshold in a consuming
app just means two places that can drift out of sync.

| Status | Meaning | Safe for autonomous use? |
|---|---|---|
| `TRUSTED` | Confidence ≥ trusted_threshold (0.90 by default) | **Yes** |
| `LEARNING` | Under active evidence gathering, not settled yet | Treat as a hint only |
| `SUSPECT` | Was `TRUSTED`, confidence has since dropped — something changed | No — investigate before relying on it |
| `STALE` | Source currently unreachable; last-known-good data retained, not deleted | Use with caution — may be outdated |
| `DEGRADED` | Profiling/validation is specifically failing for this object | No |
| `DISCOVERED` | Seen once, nothing evaluated yet | No |
| `UNKNOWN` | Confidence never converged | No — this is expected for low-signal objects, not an error |
| `DISABLED` | Source or object explicitly disabled | No |

**The graceful-degradation pattern:** don't treat a non-`TRUSTED` result
as an error or block on it. A `LEARNING`/`UNKNOWN` entity's
`semantic_name`/`description` are still useful as a *hint* for a human
or an LLM to reason about — just don't feed them into an automated
decision (a generated SQL query, a data-sensitivity classification, an
automatic join) without a human or an LLM-with-context verifying first.
`GET /v1/entities/{id}/relationships?trusted_only=true` and
`POST /v1/resolve`'s `trusted_relationships` already filter to `TRUSTED`
for you — prefer those over filtering a full list yourself.

## Sensitivity flags

`sensitivity_flags` (on both entities and fields) is a **union that only
ever grows** — once the LLM or evidence pipeline flags something as
potentially sensitive (e.g. `PII`, `FINANCIAL`), that flag is never
silently removed by a later pass, only ever added to. Treat a non-empty
`sensitivity_flags` list as a hard signal to be conservative regardless
of `status` — a `TRUSTED` entity with a `PII` flag is still something to
handle carefully (masking, access control, logging discipline), trust
status and sensitivity are orthogonal.

## Authentication (only relevant if `catalog.auth.enabled: true`)

With `catalog.auth.enabled: false` (the code default; the development
config and production both enable it) every endpoint below is
open — skip this section entirely. If the deployment you're pointed at
has turned auth on, every endpoint except `GET /health`,
`POST /v1/auth/login`, and `GET /v1/auth/matrix` needs an
`Authorization: Bearer <token>` header, and every result is filtered to
the caller's own `(level, category)` grants — an entity outside those
grants comes back as if it doesn't exist (`404`, not an empty list you
have to notice is suspiciously short). See
[DECISIONS.md](DECISIONS.md) for why.

Call `POST /v1/auth/login` with an AD username/password to get a
token; `GET /v1/auth/me` tells you whether auth is enabled for this
deployment at all and, if you're authenticated, your own resolved
grants — a good first call for any client that isn't sure. This is a
**separate mechanism** from the `grants` array you pass directly into
the optional catalog answerer's `POST /v1/ask`
([docs/integration.md](docs/integration.md)) — that endpoint never asks
this API to authenticate you, since it expects the caller (your own
backend) to have already resolved identity itself.

## Endpoint reference

| Endpoint | Use it for |
|---|---|
| `GET /health` | Liveness/readiness — always returns 200; check the body's `status` field, don't rely on HTTP status alone |
| `GET /v1/sources` | Which SQL Server sources are configured and their health |
| `GET /v1/entities?query=&status=&domain=&classification=&trusted_only=` | Browse/search entities with filters |
| `GET /v1/entities/{id}` | Full detail on one entity, including its fields |
| `GET /v1/entities/{id}/relationships?trusted_only=` | Relationships touching one entity |
| `POST /v1/search {query, limit}` | Lighter-weight than `/v1/resolve` — just ranked entity matches, no relationships/join-paths |
| `POST /v1/resolve {query, limit}` | The one-call entry point described above |
| `GET /v1/status` | Aggregate counts (by trust status, coverage, health) — what backs the status widget/UI |
| `GET /v1/suggestions?limit=` | The demand-driven coverage queue — entities real questions have hit but aren't `TRUSTED` yet, ranked by demand (see [DECISIONS.md](DECISIONS.md)) |
| `POST /v1/auth/login`, `GET /v1/auth/me`, `GET /v1/auth/matrix` | Only relevant when `catalog.auth.enabled: true` — see Authentication above |

## A minimal client loop

1. Call `POST /v1/resolve` with the user's question or a keyword
   extracted from it.
2. If `candidate_entities` is empty, say so plainly rather than guessing
   — `notes` already explains why (`"No matching entities were found in
   the catalog for this query."`).
3. Prefer `TRUSTED` candidates and `trusted_relationships` for anything
   that will run automatically (build a query, join two tables). Surface
   non-`TRUSTED` candidates as suggestions a human should confirm.
4. Use `join_paths` instead of re-deriving join logic yourself — it's
   already walked using only `TRUSTED` relationships, bounded to 3 hops.
5. If you need more than `/v1/resolve` gives you about one entity (every
   field, not just the ones relevant to the query), follow up with
   `GET /v1/entities/{id}`.

## What this API deliberately does not do

- **This API** (`api/`, the one this guide describes) never runs a
  query against your SQL Server on your behalf — it only ever returns
  catalog *metadata* (structure, inferred meaning, relationships).
  Executing a query against the actual data is the consuming
  application's responsibility, with its own credentials. The one
  exception in this project is the separate, optional `answer/`
  process's `POST /v1/ask` (code default off; the development config enables it) — see
  [docs/integration.md](docs/integration.md) — which does generate and
  execute bounded, read-only SQL, under its own guardrails and audit
  trail, and is a different process on a different port from this API.
- It never modifies anything — every endpoint here is read-only. The
  only thing that writes to the catalog is the engine's own discovery/
  validation cycle.
- It's optional, always — if the catalog is disabled or its database is
  unreachable, `/health` and `/v1/status` report that plainly (never a
  5xx from those two endpoints) rather than the API crashing. Build your
  consuming app to handle "catalog unavailable" as a normal, expected
  state, not an exception path you forgot to write.
