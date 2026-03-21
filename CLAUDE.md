# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Asana ↔ Org-mode bidirectional sync tool. Two components communicate via a JSON CLI contract:

1. **Bridge** (`bridge/`) — Python 3.11+ CLI (`asana-org-bridge`) using Typer, SQLAlchemy (SQLite), Pydantic, and structlog. Handles Asana REST API, local caching, and mutation management.
2. **Elisp** (`elisp/`) — Emacs package (`asana-org.el`) requiring Emacs 28.1+ and transient 0.4.0+. Calls the bridge via synchronous `call-process`, parses JSON responses.

The CLI contract is defined in `docs/cli-contract.md` — all JSON envelopes must conform to its v1 schema. Changes to the contract require coordinated updates in both components.

## Architecture

```
Emacs (asana-org.el)
  ├── asana-org.el          — Main commands, process invocation, error handling
  ├── asana-org-sync.el     — Bridge CLI wrappers, pending change storage
  ├── asana-org-render.el   — Org file generation, preview/apply result rendering
  └── asana-org-transient.el — Transient menus (C-c a prefix)
         │
         │ synchronous call-process, JSON over stdout
         ▼
Python CLI (asana-org-bridge)
  ├── __main__.py       — Entry point
  ├── commands.py       — Typer CLI commands (doctor, db-init, sync-pull, sync-preview, sync-apply, move-task, comment-append)
  ├── sync.py           — SyncEngine: pull/preview/apply logic, mock data
  ├── asana_client.py   — Asana REST API wrapper (requests-based)
  ├── models.py         — SQLAlchemy models (TaskSnapshot, PendingMutation, SyncRun, etc.)
  ├── db.py             — Database connection, MigrationManager (numbered SQL migrations)
  ├── config.py         — Pydantic Settings (AuthConfig, DatabaseConfig, SyncConfig, LoggingConfig)
  ├── auth.py           — AuthManager with pluggable sources (env, keyring, 1password stubs)
  └── logging_config.py — structlog setup
```

Key data flow: Emacs pulls tasks → bridge fetches from Asana API → stores snapshots in SQLite → renders to Org files. Outbound: Emacs detects Org changes → preview generates mutations → apply sends to Asana with idempotency keys.

## Common Commands

### Bridge Development (run from `bridge/`)

```bash
# Setup
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"

# Tests
pytest                                    # all tests
pytest tests/test_config.py               # single file
pytest tests/test_config.py::test_name    # single test
pytest --cov=asana_org_bridge tests/      # with coverage

# Lint & type check
ruff check src/
mypy src/

# Run CLI directly
python -m asana_org_bridge doctor
python -m asana_org_bridge sync-pull --json --incomplete-only
```

### Elisp Validation

```bash
# Byte compile (from elisp/)
emacs --batch -L . --eval "(setq byte-compile-error-on-warn t)" -f batch-byte-compile asana-org.el asana-org-sync.el asana-org-render.el asana-org-transient.el

# Checkdoc
emacs --batch -L . --eval "(progn (require 'checkdoc) (dolist (f '(\"asana-org.el\" \"asana-org-sync.el\" \"asana-org-render.el\" \"asana-org-transient.el\")) (checkdoc-file f)))"
```

## Key Design Decisions

- **Synchronous process calls**: Elisp uses `call-process` (not `start-process`) to avoid async race conditions. Stderr is discarded (`'(t nil)`).
- **Idempotency**: Every mutation has an `idempotency_key`. Request-level keys deduplicate entire requests. The `RequestIdempotency` table prevents replays.
- **Mock mode**: When `ASANA_PAT` is unset or `ASANA_ORG_MOCK_DATA=true`, the bridge returns deterministic mock data — no API calls needed for development.
- **Error envelopes**: All bridge errors follow a standard JSON envelope with `status: "error"`, error `code`, and `message`. Both success and error paths return parseable JSON when `--json` is used.
- **Schema migrations**: Numbered SQL migrations in `db.py:MigrationManager.MIGRATIONS` dict. Add new migrations with incrementing keys (`"003"`, etc.).
- **Logging**: Bridge uses structlog (JSON to stderr). Elisp logs to `*Asana Org*` buffer with PII redaction controlled by `asana-org-redact-logs`.

## Environment Variables

- `ASANA_PAT` — Asana Personal Access Token (required for live API)
- `ASANA_ORG_WORKSPACE_GID` — Workspace GID for My Tasks pull
- `ASANA_ORG_MOCK_DATA=true` — Force mock data mode
- `ASANA_ORG_DB_PATH` — Override SQLite path (default: `~/.local/share/asana-org/bridge.db`)
- `ASANA_ORG_LOG_LEVEL` — Log level (default: INFO)

## Licensing

Dual-licensed: GPL-3.0-or-later (Elisp), MIT (Bridge).
