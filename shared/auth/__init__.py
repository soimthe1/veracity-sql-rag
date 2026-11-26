"""
Authentication + (level, category) permission gate for THIS project's
own web UI. Separate from — and loosely coupled to — the catalog's own
provisional access_level/access_category shim on entities/fields
(shared/config.py's ClassificationConfig); the two
happen to share the same (int, str) shape by design.

Four parts: AD authentication, tokens, login, grants. See DECISIONS.md
for the design choices.

Entirely inert when catalog.auth.enabled is false (the default) — see
api/access.py for how the rest of the app treats that state.
"""
