# server/ — Canonical data store + legacy importer (sub-project A)

Server-side canonical SQLite schema for the MKW time-trial competition, plus a
repeatable importer for the legacy `kart-off` data. See the design spec:
`docs/superpowers/specs/2026-06-04-canonical-data-model-migration-design.md`.

## Layout
- `schema.sql` — the canonical DDL (seasons, players, runs, world_records, ...).
- `db.py` — `connect(path)` / `init_schema(conn)`.
- `courses.py` — the 30 canonical courses, `slugify`, legacy track mapping.
- `queries.py` — `recompute_is_pb`, `current_pb`, `course_leaderboard`.
- `importer.py` — `import_legacy(...)` + `python -m server.importer` CLI.

## Run the importer
    python -m server.importer --legacy-db <copy-of-hogkart.db> --out server.db

Idempotent: re-running wipes prior `legacy_import` + `carryover` rows and reloads,
leaving any `provenance='live'` rows untouched. Practice now; run once more on the
final dump at cutover.

## Out of scope here (later sub-projects)
HTTP/transport, client auth + upload, the WR scraper, live push, website/OBS,
reign computation.
