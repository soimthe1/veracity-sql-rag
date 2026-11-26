# Under the hood

One entry per key technique: where it lives, why it was chosen, and the alternative that was
considered. Engineering decisions with more history are in [DECISIONS.md](DECISIONS.md).

### Separate processes for engine, api, answer and web
- **Where:** `engine/`, `api/`, `answer/`, `web/`; one container each (`docker/`).
- **Why:** a slow source query or LLM call must never stall the catalog API. The engine has no
  HTTP port at all; the api never talks to a source or an LLM.
- **Alternative:** one FastAPI app with background tasks. Simpler, but one hung query blocks everything.

### Evidence-based trust scoring
- **Where:** `engine/` (scoring and validation cycles), states in `shared/models.py`.
- **Why:** every table and relationship carries a confidence built from evidence (declared keys,
  value-overlap tests, profiling, human confirmation) and a state such as `TRUSTED`, `SUSPECT`
  or `STALE`. The LLM proposes meanings; it never sets confidence. Answers report the weakest
  trust they relied on.
- **Alternative:** trust whatever the LLM says. Fast, but it fails silently.

### Catalog-grounded SQL generation (RAG over the schema)
- **Where:** `answer/grounding.py`, `answer/sql_generation.py`, `answer/synthesis.py`.
- **Why:** the question is first matched to the catalog (tables, columns, relationships, business
  terms). Only that grounded slice goes into the prompt, and only those tables may appear in the SQL.
  The answer is written from the rows that came back, so the numbers come from the database, not
  from the model.
- **Alternative:** send the whole schema to the model. That doesn't scale past small databases,
  and the model is free to invent tables.

### Layered guardrails for generated SQL
- **Where:** `answer/guardrails.py`, connectors in `engine/connectors/`.
- **Why:** defence in depth. The SQL must be one statement, start with `SELECT`/`WITH`, and
  contain no deny-listed keyword. It may only touch grounded tables and only the columns the
  caller may see. The service adds a row cap in the right dialect (`TOP`, `LIMIT` or
  `FETCH FIRST`). Underneath, the connector refuses non-read statements, and the source login
  itself is read-only.
- **Alternative:** a full SQL parser. More precise, but it's dialect-specific and still not a
  security boundary. The read-only login is the real boundary, and these checks fail early and
  leave an audit trail.

### Dialect-aware, multi-database connectors
- **Where:** `engine/connectors/mssql.py` (native ODBC), `engine/connectors/generic.py` (SQLAlchemy).
- **Why:** SQL Server keeps a native path for its richer metadata. PostgreSQL, MySQL/MariaDB,
  Oracle and SQLite share one generic connector, and any other SQLAlchemy dialect works by
  giving a `url`.
- **Alternative:** one connector per database. Most of that code would be duplicated.

### Search inside the catalog's own Postgres
- **Where:** `api/search/postgres_search.py`, `api/search/fuzzy.py`.
- **Why:** full-text search (`tsvector`) plus an `ILIKE` fallback. It tolerates misspellings and
  splits identifiers (`EmpQualification` matches "employee qualification"). It needs no extra
  service and no extension, so it runs on managed Postgres.
- **Alternative:** Elasticsearch or a vector database. That means more infrastructure for a
  corpus of a few thousand objects.

### (level, category) access model
- **Where:** `shared/access/`, `shared/auth/grants.py`, `config/levels.yaml`.
- **Why:** each table and column gets a cell such as `20B` (level 20, category B). People and
  roles are granted cells by exact match, with no inheritance and default-deny (unresolved
  objects land in a quarantine cell nobody can hold). Cells are assigned by human decision,
  rule, inheritance, AI proposal or naming convention, strongest first.
- **Alternative:** plain role-based table grants. Simpler, but they can't express "HR data,
  confidential tier".

### Sign-in: none, local or LDAP
- **Where:** `shared/auth/` (scrypt password hashing, HS256 tokens), `api/routers/auth.py`.
- **Why:** one switch (`catalog.auth.mode`). `local` keeps accounts in the catalog database and
  creates the first administrator on the first visit, using a one-time code printed to the API
  log. `ldap` binds against AD/LDAP. Administration is not clearance: an admin manages people
  but sees data only through granted cells, unless configured otherwise.
- **Alternative:** an external identity provider only. Right for big organisations, too heavy
  for a laptop.

### Configuration: one YAML, secrets only in the environment
- **Where:** `shared/config.py`, `config/`.
- **Why:** every non-secret setting is in `config.yaml`. `config.prod.yaml` and a git-ignored
  `config/local/` are merged on top. A secret can only be a `${VAR}` reference; a literal
  secret, or an unknown key, stops startup with the key named.
- **Alternative:** environment variables for everything. Hard to review and easy to typo.

### Migrations at startup
- **Where:** `migrations/` (Alembic), run by engine, api and answer when they start.
- **Why:** a new version upgrades its own schema, and a fresh install needs no manual step.

### Supply chain and release hygiene
- **Where:** `scripts/release_check.py`, `.githooks/pre-push`, `.dockerignore`.
- **Why:** before every push, the release check scans every committable file for private IP
  addresses, e-mail addresses and a private word list. The hook also refuses commits containing
  private paths. Images never contain `.env` files or private config. Base images use fixed version tags.
