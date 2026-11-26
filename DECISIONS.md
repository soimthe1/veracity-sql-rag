# Design decisions

This records the *why* behind choices that weren't purely dictated by
the original specification, so a future maintainer (human or AI) doesn't
have to reverse-engineer the reasoning from code. "Spec section" numbers
below refer to that original (unpublished) specification.

## Two processes, not one

`engine/` (discovery/inference scheduler) and `api/` (REST API) are
separate Python processes, sharing only the `shared/` library — never a
single FastAPI app with a background scheduler bolted on. Reasons, in
order of how much they mattered:

1. **Fault isolation.** A hung SQL Server connection, a slow/looping LLM
   call, or a bounded-profiling query that takes longer than expected must
   never have any way to make `/health`, `/v1/entities`, or any other API
   route slow or unresponsive. Two processes means an OS-level guarantee
   of this, not a "we were careful with async" hope.
2. **Independent scaling/restart.** The API can be scaled horizontally or
   restarted on deploy without interrupting an in-flight discovery cycle,
   and vice versa.
3. **Consistency with established practice.** A separate collector
   process and API process is a common, proven split for monitoring-style
   tools; matching an established, working pattern beats inventing a new
   one.

The cost is real (two processes to run, two Dockerfiles, a shared-library
versioning discipline) and was accepted deliberately, not overlooked.

**A third process, `answer/`, was added later for the identical reason.**
It does a slice of what each of the other two does (reads the catalog
Postgres like `api/`, talks to a SQL Server source and an LLM like
`engine/`) specifically because `POST /v1/ask` can involve an LLM call
*and* a live query on the request path — exactly the kind of thing
reason 1 above says must never share a process with `/v1/resolve` or
`/v1/entities`. Folding it into `api/` would have meant a slow answerer
request could make the stable metadata API feel slow too; folding it
into `engine/` would have given it no HTTP surface at all. Three
processes, sharing only `shared/`, keeps every existing guarantee about
`api/`'s responsiveness intact while adding the new capability. See
[docs/integration.md](docs/integration.md) for what it does.

## A separate frontend app, built with Vite + React

The frontend is kept independent of any other application's frontend:
`web/` is its own small app with its own `package.json`, never a route or
component added to another project's bundle.

A bundler-free vanilla-JS page was considered and rejected in favour of
Vite + React + TypeScript, which suits a UI of this size (many screens,
two languages, shared components). The two decisions (separate app vs.
shared bundle; which toolchain) are independent.

## The coverage-percentage knob

The requirement was specific and, at the time, unusual
for this kind of tool: instead of a human enumerating which databases or
tables matter, expose **one percentage** and delegate to the system
("assume best solution... how much is needed to get most of the
answers"). That produced `catalog.coverage.deep_analysis_target_pct`
(config/config.yaml), with this shape:

- **Universal Level 1** (name, columns, types, declared keys,
  dependencies, fingerprint) runs on *every* discovered object,
  regardless of the knob — cheap, metadata-only, and it's what makes
  fingerprint-based incremental re-discovery possible at all.
- The knob controls what fraction of objects additionally get
  **Level 2** (bounded profiling, value-overlap relationship testing,
  LLM semantic inference) — the expensive part.
- Objects are ranked by a computed `relevance_score` (declared FK
  degree, naming signal, classification, object type) before the
  percentage cutoff is applied, so raising the knob from 30% to 80%
  doesn't add a random 50% more objects — it adds the *next-most-likely-
  relevant* ones.
- `always_deep_categories` (BUSINESS_ENTITY, RELATIONSHIP_TABLE) and
  `never_deep_categories` (SYSTEM_TECHNICAL, TEMPORARY, STAGING) are
  overrides that fire before the percentage math, so the knob can never
  starve the tables that matter most or waste budget on tables that
  structurally never will.
- `min_deep_objects_per_source` / `max_deep_objects_per_source` (25 /
  2000) exist so the percentage behaves sanely at both extremes — a
  5-table database and a 5,000-table database shouldn't both be governed
  by the same raw percentage without floors/ceilings.

**Why 30% as the default**, following the requirement's own framing
("balance... how much is needed to get most of the answers"): most
production OLTP schemas concentrate their genuinely ambiguous, business-
meaning-bearing tables in a minority of the total object count — the
rest is reference/lookup data (self-describing), system/temp/staging
objects (excluded outright), or straightforward child tables whose
relationships are already fully explained by a declared FK (Level 1 +
`explicit_fk` evidence alone reaches TRUSTED with no Level 2 work at
all — see confidence weights below). 30% deep analysis was reasoned to
cover the ambiguous minority without paying deep-analysis cost on
objects Level 1 + declared constraints already resolve. It's a starting
point, not a proof — the mechanism (reevaluate_on_change: true, so
raising the knob only enqueues the *newly included* slice rather than
re-running everything) is what makes "start at 30%, raise to 80% if
answers feel incomplete" a cheap, safe operation rather than a full
re-discovery. That operational cheapness is deliberate compensation for
not being able to prove 30% is optimal in the abstract.

## Confidence weights, and why `explicit_fk` alone reaches TRUSTED

`catalog.confidence.weights` (config.yaml) is tuned so that a
SQL-Server-enforced foreign key (`explicit_fk: 0.92`) crosses
`trusted_threshold` (0.90) by itself — a declared FK is closer to
ground truth than anything this system could infer, so it shouldn't
need corroborating evidence to be trusted. Every other weight was tuned
against the spec's own worked example (a fully-corroborated relationship
combining naming similarity, LLM interpretation, a confirming view
definition, value overlap, datatype compatibility, and procedure usage
should reach ~0.95, not fall short of `trusted_threshold`) — see the
git history for the specific retuning pass; the numbers in
config.yaml are the validated result, not an initial guess.

`view_definition_confirms` and `procedure_usage_confirms` scale to full
weight at *one* confirming occurrence, not several — the spec treats a
single confirming view as strong standalone evidence, distinct from
`executed_join_evidence`, which the spec explicitly frames as needing
repetition (scales to full weight at 3+ observed joins) since one
observed join could be coincidental in a way one written view
definition referencing two tables together isn't.

## SUSPECT vs. LEARNING/UNKNOWN

`engine/confidence.py`'s `next_status()` reserves `SUSPECT` for a
**regression** — something that was previously TRUSTED and dropped. A
brand-new object with low initial confidence goes to `LEARNING` or
`UNKNOWN` instead, never `SUSPECT`, because `SUSPECT` is meant to signal
"this used to be more certain than it is now," which is a materially
different (and more actionable) situation than "this has never been
resolved."

## No bundled infrastructure

By design, this module never wraps, bundles, or manages its databases or
any other infrastructure — `docker-compose.yaml` defines exactly the four
application services (engine, api, answer, web) and nothing else. Every
data dependency is reached outward via config: the catalog's own Postgres
(`catalog.database`), SQL Server sources (`catalog.sources`), and the
LLM endpoint (`ai.default`). Anything else (vector stores, graph or
time-series databases) is out of scope entirely, not just unbundled.

## Search: Postgres only

`api/search/postgres_search.py` is the only search backend — it
requires nothing beyond the catalog's own Postgres (built-in
tsvector full-text plus ILIKE; deliberately no pg_trgm or other
extension, which managed/shared Postgres may not allow), which is simplest to run and good enough for the catalog's realistic
data volume (thousands, not millions, of entities). There are no
search indexes on purpose: the OR'd leading-wildcard ILIKEs force a
scan anyway, which is milliseconds at this size. Revisit (generated
tsvector column + GIN) only if `EXPLAIN ANALYZE` shows search is slow.
ElasticSearch was considered and rejected (2026-11): the catalog is
relational and small, and a second store would need syncing and a copy
of the access filter. `api/search/base.py`
still defines a `SearchProvider` interface and `api/search/factory.py`
builds the provider, so a second backend could be added
later without touching `/v1/search`, `/v1/resolve`, or
`answer/grounding.py` — but nothing beyond Postgres is implemented or
configured today.

Postgres search uses **OR semantics across query terms**, not AND — a
multi-word query like "employee assignments" matches an entity
containing either word, not only one containing both. Early in a
catalog's life most entities are only partially described (semantic_name
filled in before description, or vice versa), so requiring every query
term to match a single row would make search go quiet exactly when it's
most needed — during LEARNING, before the evidence/LLM pipeline has
filled in every field.

## Two LLM providers with explicit thinking suppression

Every LLM call goes through `shared/llm/base.py`'s `LlmProvider`, a thin
wrapper over `shared/ai_providers.py` that serves the `ai.default`
profile in config.yaml: Ollama (`provider: ollama`) or any
OpenAI-compatible endpoint such as vLLM (`provider: openai`).
`suppress_thinking: true` suppresses reasoning output on both — Ollama's
native `think` field, and, for OpenAI-compatible endpoints, both
`chat_template_kwargs.enable_thinking` *and* a `/no_think` prompt suffix
— because different serving stacks honor different mechanisms, and batch
catalog inference (many small LLM calls per discovery cycle) should not
pay a reasoning-model's token and latency overhead. Any
`<think>...</think>` block that slips through anyway is stripped, so a
misconfigured or unusually stubborn model still can't break the
strict-JSON-only response contract. A failed or timed-out call never
raises: it returns `ok=False` and the caller falls back to
structural-only cataloging.

## Raw SQL text (views/procedures) is never persisted

`engine/relationships.py`'s join-predicate mining reads view and
procedure definition text at discovery time to extract join predicates
as evidence, then discards the text — only the derived evidence
(`JoinPredicateEvidence` rows, confidence contributions) is written to
the catalog database. This is a deliberate data-governance choice: an
air-gapped, high-security deployment should not have to trust this module
with a second copy of production source code sitting in a database that
isn't the system of record for that code.

## Alembic auto-upgrade, not manual migration scripts

Following the requirement ("fully tolerant and
self-evolving... automatically generating/applying all relevant SQL
schema changes"), `shared/db.py`'s `init_db()` runs `alembic upgrade
head` automatically at process startup, wrapped in a Postgres
session-level advisory lock (key `913_224_001`) so the engine and api
processes starting concurrently — the normal case in
`docker-compose.yaml` and `scripts/dev.ps1` — can't race each other's
DDL (concurrent migration runs are a known failure mode for
multi-process services). There is still exactly one hand-authored migration
(`migrations/versions/0001_baseline_schema.py`, verified against an
empty `alembic revision --autogenerate` diff) — "self-evolving" means
future schema changes should be generated and applied automatically as
`shared/models.py` evolves, not that the baseline itself was generated
blind.

## The catalog answerer: provisional classification, and a guardrail that's a heuristic on purpose

Two choices in `answer/` and `engine/access_classification.py` are
worth spelling out, since neither was purely dictated by the original
spec — this capability came from a later requirement, summarized in
[docs/integration.md](docs/integration.md).

**Provisional `(level, category)` shim, not a real permission system.**
The requirement was explicit: infer classification from
`sensitivity_flags` *now*, add the real schema fields *now*, and treat
the actual governance system as a later replacement — "security we can
patch later also, but yes level, category is the ultimate end goal so
keep the room open for this." `access_classification_provisional`
(always `True` in this build) exists specifically so that later system
can tell, per row, whether it has ever taken ownership of a
classification, without needing a new column or a migration to ask
that question. The gate itself (`answer/grounding.py`'s exact-match
filter against the caller's grants) is enforced for real today — only
the *values* behind it are provisional, never the enforcement.

**The SQL guardrail (`answer/guardrails.py`) is a bounded heuristic, not
a SQL parser**, deliberately. A real T-SQL parser is a much larger
dependency for a fairly narrow job, and the guardrail is explicitly
**one layer of defense, not the only one** — the source login's own
read-only DB grant (same convention as the engine's own connector, see
`README.md`) is the actual backstop. A regex-based check that
occasionally over-rejects a valid query (fails closed) is the right
trade-off for that role; a regex-based check that occasionally
under-rejects would not be, which is why every check is deliberately
conservative (whole-word keyword matching, `FROM`/`JOIN`-target
extraction rather than trying to parse every expression, refusing
outright rather than guessing when a check can't determine an answer).

**One chunk per question, not one per row.** The answer is a RAG-style
context chunk (chunk_id, doc_id, text, level, category, score, ...) so a
retrieval pipeline can cite it like a document passage, and a chunk is
meant to be one coherent, citable unit. Emitting one row as one chunk
would produce, for a 20-row query, twenty near-duplicate citations for
what is really one answer ("employee 4821's record") — a single rendered
block reads like any other document chunk and keeps the caller's
context-building and citation numbering unchanged.

## The web UI's own login and permission matrix

Two related but separate things were added on top of
the original spec: a login page for the web UI, and "a (level,
category) permission matrix mechanism" for this app itself, in the same
shape a trusted server caller (another application that resolves its own
users) already uses for its grants: roles, a levels.yaml lattice, AD
authentication, tokens, login and grants. Grant scope was
left open in the requirement ("may be we will
use the same, or have the similar permission matrix here also") —
resolved here by building this app's own `shared/auth/grants.py` as a
**parallel lattice with the identical shape** (`Grant`, `GrantSet`,
exact-match on `(level, category)`, no inheritance on either axis,
grants attach to roles and combine by union) rather than depending on
any caller's code. This app has no runtime dependency on its callers (see
docs/integration.md), so sharing a Python package isn't an option;
sharing the *shape* is. An operator who wants one coherent scheme end to
end can point `catalog.auth.levels`/`.categories` at the exact same
values a caller already uses; an operator who wants this UI's users
governed completely separately can just as easily leave them different. Both are first-class, not one
picked and the other closed off.

Grants attach to `AppRole`s, never to `AppUser`s directly, and a user's
effective grants are the union across their roles. `is_admin` is a property of a role, not a grant, and it
is **never a read bypass**: `require_admin()` gates the Security tab's
CRUD (`api/routers/auth.py`) and the admin-only
`GET /v1/sources/config`, but an admin's own `/v1/entities`,
`/v1/search`, `/v1/resolve` results go through the exact same
`filter_entities_query()` as anyone else's. This was verified, not just
designed — the e2e test explicitly asserts a role with `is_admin: true`
and zero read grants still sees zero entities.

`catalog.auth.enabled: false` (the code default; the development
config enables it) doesn't take a different
code path per router — every request resolves to a single
`UNRESTRICTED` `Principal` (`api/access.py`) whose `GrantSet`
(`UnrestrictedGrantSet`) always answers `.allows() == True`. Auth-on
and auth-off both flow through the same `filter_entities_query()`; the
only thing that changes is which `Principal` the dependency hands back.
One function to reason about, not two behaviors to keep in sync.

## AD authentication and session tokens

`shared/auth/ad_auth.py` is deliberately minimal — a plain LDAP/LDAPS
simple bind (`ldap3`), nothing more.
It returns a bare `bool`, never a reason, so a network hiccup and a
wrong password are indistinguishable to the caller; the real reason is
logged and written to `AppLoginAudit`, never returned in the response
(`shared/auth/login.py`'s `_GENERIC` message is the same string for
every failure mode, matching this project's existing anti-enumeration
posture on `GET /v1/entities/{id}` — see CATALOG_CONSUMER_GUIDE.md).

Login **never self-provisions an account.** A successful AD bind alone
is not enough — an `AppUser` row with at least one role must already
exist, or login fails with the same generic message as a wrong
password. The first admin has to be created out of band, once, by
whoever deploys this: `scripts/bootstrap_admin.py` (idempotent). There
is deliberately no "first run" setup wizard in the UI — for an
air-gapped, high-security deployment, one documented CLI script run by an
operator with shell access is a smaller attack surface than a
web-reachable bootstrap flow, even a one-time one.

The JWT (`shared/auth/tokens.py`) carries **identity only** — `sub`,
`username`, `name`, `iat`, `exp`, `jti` — never roles or grants.
`principal_from_token` re-resolves the user's roles and grants from
Postgres on every single request. This costs a small query per request
in exchange for never having a stale-permissions window: revoking a
role, disabling a user, or editing a grant takes effect on that user's
very next request, not at their next login hours later — which matters
more for a tool whose own Security tab lets admins change permissions
live than the extra query costs.

**Self-role-edit prevention.** `PATCH /v1/auth/users/{id}` explicitly
refuses to change the caller's own role assignments (compares
`current_role_ids` against `requested_role_ids` when
`str(user.id) == principal.user_id`) — an admin can't accidentally (or
deliberately) lock themselves out or silently self-escalate further.
`web/src/components/Security.tsx`'s `UsersPanel` mirrors this in the
UI — the role checkboxes are disabled with a visible explanation when
editing your own row, rather than letting the person submit and only
then discover the 400.

## Bearer token first, not cookie-reliant

`web/src/auth.tsx`/`api.ts` store the session JWT in `sessionStorage`
and send it as an `Authorization: Bearer` header on every request,
rather than relying on the `httponly` cookie `POST /v1/auth/login` also
sets. This was the simpler choice for this project's dev topology (web
on `:5180`, api on `:8100` — genuinely cross-origin in dev), sidestepping
cookie/CORS/SameSite configuration entirely instead of routing dev
traffic through a proxy just to make cookies same-origin. The cookie is
still set on login and still works for any other caller of this API
that prefers cookie auth; this app's own frontend just doesn't depend
on it, so nothing here breaks if a deployment's reverse-proxy topology
changes later. `sessionStorage`, not `localStorage`, so a closed tab
ends the session but a refresh doesn't — the short-session posture of
an internal admin tool with elevated CRUD (roles/users), not a
"remember me for 30 days" consumer pattern.

## Instance-level-only sweeping, extended into the UI

`GET /v1/sources/config` (admin-only) and the Sources tab's rich cards
surface exactly the instance-level configuration README.md
already documents — host, port, driver, the SQL login's *username*
(never its password), TLS, timeout, and the `databases_to_catalog`
autodiscovery policy — and stop there, deliberately. This was an
explicit requirement ("purposely not doing at database level... too
much database level control will make the app unnecessarily
complicated"), and it's the same argument already made for
`deep_analysis_target_pct` above: a human enumerating which databases
or tables matter is exactly the manual-curation problem the coverage
percentage exists to avoid. Giving the UI a place to configure sweeping
doesn't reopen that door just because there's now somewhere to put a
per-database toggle — which databases/objects get deep analysis stays
entirely the job of the coverage-percentage + classification machinery,
now additionally informed by real demand (next section).

## The demand-driven, self-correcting coverage loop

The requirement's own definition, quoted directly: *"the system
should learn if system was not able to answer mark it, and too many
queries come for the same thing, the system will decide to sweep it if
its not available thats what we call self correcting."* `engine/coverage.py`'s
percentage-and-category selection (see the coverage-knob decision
above) picks what to deep-analyze from what's already discovered, but
had no way to react to what real questions actually ask for — this
closes that loop.

`answer/grounding.py` bumps `unanswered_hit_count`/`last_unanswered_at`
(`shared/models.py`, `CatalogEntity`) on every `TABLE`/`VIEW` a
question's search matches that isn't yet `TRUSTED` —
**regardless of the asking caller's own access grants.** This is
deliberate: it's a signal about the *catalog's coverage*, not an
access-control decision, so a question nobody was permitted to have
answered still tells the operator "people keep asking about this and
the catalog doesn't have it yet" without leaking the answer itself to
anyone who lacked the grant for it.

`compute_deep_analysis_selection` reads the counter back on the next
validation cycle. Past `demand_promote_threshold` (10 by default), an
entity is force-promoted into deep analysis exactly like
`always_deep_categories` — except `never_deep_categories` still wins
even here, deliberately, so demand can never pull a category excluded
on governance or performance grounds back into deep analysis just
because people keep asking about it. Below the threshold, a capped
boost (`demand_rank_boost`, capped at `demand_rank_boost_cap`) nudges
the entity up the percentage-cutoff ranking instead of doing nothing
until the threshold is crossed — this is what actually reflects "too
many queries come for the same thing" as a gradient rather than a hard
cliff at exactly 10. The counter resets to 0 once an entity reaches
`TRUSTED` — it's queue pressure for what still needs to happen, not a
permanent popularity score.

`GET /v1/suggestions` (and the web UI's **Suggestions** tab) surfaces
the live queue, ranked by hit count, with a `will_force_deep_analysis`
flag computed from the same threshold/override logic the engine itself
uses — an operator watching it sees exactly what the next validation
cycle is about to do, not a UI-only approximation of it.

## The data access realm: one cell per object, bucketed in layers, humans always win

**Decision.** Keep a self-contained permission model inside this app
rather than delegating to an external service: a matrix of levels (10..120) ×
categories (A..Z, Z = Quarantine), exact match, no cascading on either
axis. Roles are granted cells; people are added to roles. Every
database, schema, table and column sits in exactly one cell. All of it
lives in this app's own database (`app_roles`, `app_role_grants`,
`app_users`, `catalog_access_labels`, `catalog_access_rules`,
`app_access_audit`), so the app runs standalone and can grant people
access without any other system involved. AD is used only to prove
who someone is at sign-in (and, optionally, to look people up when
adding them).

**Why not per-table role assignment.** Thousands of tables × many
roles is unmaintainable. Objects are bucketed into a small grid instead,
mostly automatically, and roles only ever reference cells.

**How an object's cell is decided** (per axis, first opinion wins):
manual placement → admin rule (glob patterns on source/database/schema/
table/column, type, domain, flags) → inherited from a parent's *human*
decision (database → schema → table → column) → AI proposal (sensitivity
flags → level snapped up to a tier, category from flags or business
domain) → naming conventions from `levels.yaml` → default cell (120Z,
visible to nobody). Labels are materialized on `catalog_entities` /
`catalog_fields` with their provenance, so enforcement is an indexed
column compare, never a recomputation.

**AI never overrides a human.** When the AI thinks an object is more
sensitive than, or in a different category from, a human decision, the
object goes to the Review list and stays where the human put it until
someone accepts or keeps it. A "keep" is remembered against that exact
proposal and reopens only if the proposal changes.

**Enforcement.** A column is visible only if both its own cell and its
table's cell are held. Grounding withholds restricted columns from the
LLM; the SQL guardrail rejects any query that touches them (including
`SELECT *` on a table with hidden columns). Answers are labelled with the
most sensitive cell they drew on. Administrators see all data
(`catalog.access.admins_see_all_data`).

**First admin.** A built-in, undeletable Administrator role is seeded by
migration 0007. `catalog.auth.bootstrap_admins` makes a listed account an administrator on
its first successful AD sign-in; `scripts/bootstrap_admin.py` does the
same from the command line. The last active administrator can't be
removed or deactivated, and nobody can change their own roles.

**Who sees which screens.** People without an admin role get the
workspace only — Ask today (Search and Create are the planned
additions) — in a focused layout with no navigation rail. Admins get the
full app, with the rail grouped into Workspace, Catalog and
Administration. Which extra screens non-admins may open is one setting,
`catalog.access.non_admin_screens` (default `["ask"]`), enforced by the
API (403) as well as the menu. Non-admins are told how much *they* can
query, never the size of the rest of the estate. `/v1/status` now needs a
signed-in caller as well.

**Performance.** Recompute is set-based (Core rows, one temp-table
`UPDATE … FROM`, md5 signature per row to skip unchanged ones):
~7.5 s for a first pass over 100k columns, ~2.4 s when nothing changed;
the bucket grid is a `GROUP BY` (~66 ms).

## Ask is the home page; Create lives on every answer; one audit record per question

**Ask is the landing page for everyone.** It shows only real things:
suggestions built from the catalog (grouped by the shape of question the
pipeline can answer — breakdowns, counts, latest records, reference
lists), the person's own recent questions from the question log, their
access (or, for administrators, live catalog health), how answers are
made, and tips. A database picker limits an answer to one database.
There is no separate Search — asking is searching.

**Create** turns an answer into an email draft, a Word document, a
PowerPoint deck or a PDF. Word and PowerPoint are built by the answer
service (`answer/exports.py`, python-docx / python-pptx); the email draft
and the PDF (a print-ready page) are built in the browser so Arabic
renders exactly as on screen with no fonts to ship. Every document
carries the answer's classification in its header/footer or on every
slide, taken from the audit record, never from the browser. Only the
person who asked (or an administrator) can create from an answer, and
every document created is logged.

**Generated SQL is shown only to people who need it** — administrators,
and roles with the "Can see generated SQL" flag (developers, SEs). The
server leaves it out of the response for everyone else; it is not merely
hidden on screen.

**The audit.** `catalog_ask_log` has one row per question (who, their
cells, the cells and tables the answer drew on, its classification,
outcome, trust, counts); `catalog_ask_audit` keeps one row per generated
query with the exact SQL; `catalog_ask_exports` one per document created.
The answer's data rows are never stored — that would be a second copy of
sensitive data. Cost: one small insert per question and per query,
negligible next to the LLM and the query. Security > Question audit
filters by person, period, outcome and classification, and flags blocked
attempts, withheld columns and documents created.
`catalog.answer.audit_retention_days` (default 0 = keep forever) prunes
old records daily.

## What was tested, and how

The four images are built by `.\scripts\dev.ps1 build`, which also saves
them to one `.tar` for the target host; both compose files are validated
with `docker compose config`.

The `answer/` process was exercised end-to-end during development: both Alembic migrations were applied to (and
autogenerate-diffed cleanly against, and downgraded/re-upgraded on) a
real scratch PostgreSQL instance, `answer/guardrails.py` was run
against a table of SQL fixtures (multi-statement injection, deny-listed
verbs, out-of-whitelist tables, missing/present `TOP`, a `WITH ...`
CTE), and a full `POST /v1/ask` request — grounding, SQL-generation
prompt construction, guardrail validation, a stubbed executor and LLM,
and audit-row writing — was run through FastAPI's `TestClient` against
that same real Postgres for three cases (no grants, mismatched grants,
matching grants), all producing the expected default-deny /
access-filtered / successful-chunk outcomes with matching
`catalog_ask_audit` rows. Real SQL Server connections and real LLM calls
are exercised by running the services against actual infrastructure.

## Cell spelling: level then category ("20B")

Cells are written level then category: `20B`, `40A`, `10C` (the same
order as a `.../20/B/...` folder convention). This app uses exactly that spelling in API responses, stored values, audit
rows, logs, error messages and UI chips. The single definition is
`shared/access/cells.py`, and `web/src/lib/lattice.tsx` mirrors it.

- **Strict on order, forgiving on case (changed 2026-11-12).** Input
  `20b`, ` 20B ` reads as `20B`, and structured grants accept
  `{"level": "20", "category": "b"}` or a bare `"20b"`: a lower-case
  letter can only mean one cell, so nothing is guessed. `B20` is still
  not `20B`: it is a 400 (or a config error for `cell_labels`) that names
  it, as is any grant that is not one level 0-9999 plus one letter (a 422).
  Never re-read, never skipped: a skipped grant is silently fewer rows,
  which looks exactly like "no access". Stored and output cells are always
  upper case; the database CHECK and startup check stay strict.
- **One stored form.** Migration 0009 rewrote every stored cell text
  (ask log cells, dismissed AI proposals, cell history, access audit) and
  then verifies. Tables' and columns' own cells were always the
  (int, str) pair. `AskIn.grants` stays structured `[{level, category}]`.
- **Startup check.** API, answer service and engine refuse to start if any
  stored cell is not level-first, or a category column is not a single
  upper-case letter (`shared/access/cell_check.py`).
- A placement or rule that sets one axis only is written `20*` / `*B`. A
  dismissed AI proposal is written `20B`, `20-` or `-B`.

## Production security posture (2026-10, after a security audit)

- **Production always signs in** (`auth.enabled: true` in the merged
  production config). With sign-in off, every request is an unrestricted
  administrator and Ask trusts any grants it is sent, which is acceptable
  only on a developer's machine. A test (`test_production_config_is_locked_down`)
  loads config.yaml + config.prod.yaml the way production does and fails
  if the result loosens this.
- **Administration is not clearance**:
  `admins_see_all_data` defaults to false and is false in production. Admins
  manage everything, but in Ask and the catalog screens they see only the
  cells their own roles grant. Development keeps it true, explicitly.
- **Bootstrap admins make the first administrator only.** A listed account
  is auto-registered only while no active administrator exists. It was
  previously "while the account has no roles", which let a demoted account
  promote itself again.
- **No `"*"` CORS.** Browsers send the session cookie cross-origin, so the
  config loader refuses `"*"`. Development lists the localhost UI origins;
  production lists the UI's https origin in `config.prod.yaml`.
- **AD over TLS is verified in production** (`ldap.tls_verify`, CA from
  `ldap.ca_certs_file` or the OS store). The session cookie is
  `Secure` in production (`auth.cookie_secure`).
- **The database enforces cell spelling** (migration 0010 CHECK
  constraints). The startup check stays as a second line.
- **`/v1/ask/scope`** is the "has database access?" signal. Suggestions
  also need names and trust, so an empty list never means "no access".
- Accepted by design: a service-key holder may name any user in
  `X-On-Behalf-Of`. The key is a fully trusted server credential: it can
  already send any grants. Protect it, and rotate it if it leaks.
