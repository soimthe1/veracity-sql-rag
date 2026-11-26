# Calling Veracity SQL from another application

The answer service (`answer`, port 8200) is usable by any backend: a RAG pipeline, a chatbot,
a reporting job. It turns a plain-language question into one or more bounded, read-only SQL
queries, runs them, and returns the result as context chunks plus a ready-made answer.

It is optional and loosely coupled: if it is down or cannot answer, the caller gets an empty
result and carries on.

## Authentication

| Sign-in mode (`catalog.auth.mode`) | How a caller is identified |
|---|---|
| `none` | Nobody is identified. The `grants` in the request body are used as sent. Laptop/demo only. |
| `local` / `ldap` | Either a signed-in user's session token (`Authorization: Bearer …`), whose access is resolved from this app's own roles, **or** a trusted server caller (below). |

**Trusted server caller.** Set a long random value in `CATALOG_ANSWER_SERVICE_KEY` (`.env`) and
give it to the calling application. It then sends:

```
X-Service-Key: <the key>
X-On-Behalf-Of: <the end user's account name>     (optional; used for the audit and history)
```

Only a caller with the key may pass `grants` in the body. Without the key, body grants are
ignored and the request needs a user session.

## `POST /v1/ask`

```json
{
  "question": "Which product category had the most revenue last quarter?",
  "grants": [{"level": 20, "category": "B"}, "10A"],
  "limit": 5,
  "database": "sales"
}
```

- `grants`: the (level, category) cells the end user is cleared for (see `config/levels.yaml`).
  Empty means no data access: the service returns no chunks rather than guessing.
  Each grant may be an object or a string such as `"20B"` (level, then category).
- `database`: optional; restricts the search to one database.

Response (abridged):

```json
{
  "answer": "Electronics had the most revenue last quarter: 182,340.50.",
  "trust_label": "TRUSTED",
  "trust_score": 0.91,
  "chunks": [{
    "chunk_id": "…", "doc_id": "…", "origin": "database",
    "text": "Database result for: …\nTables: sales.orders, sales.order_items …",
    "level": 20, "category": "B", "score": 0.91,
    "title": "…", "trust_label": "TRUSTED", "trust_score": 0.91
  }],
  "took_ms": 2140.5,
  "notes": "…"
}
```

The chunk is shaped like a retrieved document passage (`chunk_id`, `doc_id`, `text`, `level`,
`category`, `score`, `title`, `source_uri`, `heading_path`, `page`, `origin`), so a retrieval
pipeline can cite it next to document passages without new code. Its `level`/`category` is the
most sensitive cell among the tables and columns it used: file the chunk under that cell.

An ordinary "couldn't answer" (no matching tables, the model declined, a guardrail rejected the
SQL) is a `200` with no chunks, never an error.

## Other endpoints

| Endpoint | Purpose |
|---|---|
| `GET /v1/scope` | Does this caller have any database access at all? (cheap pre-check) |
| `GET /v1/suggestions` | Example questions for the caller's databases |
| `GET /v1/recent`, `GET /v1/history` | The caller's past questions |
| `POST /v1/create` | Turn an answer into a Word or PowerPoint document |
| `GET /health` | Liveness and dependency status |

Interactive documentation: `http://<answer-host>:8200/docs`.

## Deep link into the web UI

`https://<ui-host>/?q=<question>&db=<database>` opens the Ask page and asks the question
straight away, so a calling application can offer "Open in Veracity SQL".
