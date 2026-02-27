# Asana Org Bridge CLI

Local bridge CLI for Asana ↔ Org-mode integration (Option B architecture).

## Overview

The bridge CLI is a Python-based tool that:
- Pulls tasks from Asana and caches them in SQLite
- Provides sync commands for preview/apply workflow
- Supports PAT authentication from environment or secure sources
- Uses structured JSON logging with PII redaction

## Installation

### Using uv (recommended)

```bash
cd bridge
uv pip install -e .
```

### Using pip

```bash
cd bridge
pip install -e .
```

## Configuration

The bridge reads configuration from environment variables or `.env` file.

### Required: Authentication

Set your Asana Personal Access Token:

```bash
export ASANA_PAT="your_pat_here"
```

Or create a `.env` file in the project root:

```bash
ASANA_PAT=your_pat_here
```

### Optional: Database

```bash
# Custom database path (default: ~/.local/share/asana-org/bridge.db)
ASANA_ORG_DB_PATH=/path/to/bridge.db
```

### Optional: Sync Settings

```bash
# Use mock data instead of API calls
ASANA_ORG_MOCK_DATA=true

# Retention days (defaults shown)
ASANA_ORG_SNAPSHOT_RETENTION_DAYS=30
ASANA_ORG_JOURNAL_RETENTION_DAYS=90
ASANA_ORG_AUDIT_RETENTION_DAYS=180
```

### Optional: Logging

```bash
# Log level: DEBUG, INFO, WARNING, ERROR
ASANA_ORG_LOG_LEVEL=INFO

# Use JSON structured logging (default: true)
ASANA_ORG_LOG_FORMAT_JSON=true

# Redact PII from logs (default: true)
ASANA_ORG_LOG_REDACT_PII=true
```

## Commands

### `doctor`

Run diagnostics to verify bridge setup:

```bash
asana-org-bridge doctor
```

Checks:
- Python version
- Configuration loading
- Database initialization
- Authentication setup

### `db-init`

Initialize the database with schema migrations:

```bash
asana-org-bridge db-init
```

Creates tables:
- `schema_meta` - Schema version tracking
- `tasks_snapshot` - Task snapshots from Asana
- `org_mirror_state` - Org file mirror state
- `pending_mutations` - Queued mutations
- `sync_runs` - Sync operation journal

### `sync-pull`

Pull tasks from Asana and update local cache:

```bash
# Standard pull
asana-org-bridge sync-pull

# Force pull even if recently synced
asana-org-bridge sync-pull --force

# Limit number of tasks
asana-org-bridge sync-pull --limit 10
```

In mock data mode (default without ASANA_PAT):

```bash
# Explicitly use mock data
ASANA_ORG_MOCK_DATA=true asana-org-bridge sync-pull
```

### `sync-preview`

Preview pending changes before applying:

```bash
asana-org-bridge sync-preview

# JSON output for Emacs contract
asana-org-bridge sync-preview --json
```

Shows:
- List of pending mutations
- Conflicts (if any)
- Warnings

### `sync-apply`

Apply pending mutations to Asana:

```bash
# Dry run
asana-org-bridge sync-apply --dry-run

# Apply approved mutation set from stdin JSON
cat request.json | asana-org-bridge sync-apply --json -
```

## Development

### Running Tests

```bash
cd bridge
uv pip install -e ".[dev]"
pytest
```

### Code Quality

```bash
# Lint
ruff check src/

# Type check
mypy src/
```

## Architecture

```
┌─────────────────────────────────────────────┐
│           Emacs (asana-org.el)              │
└─────────────────────────────────────────────┘
                      │
                      │ Elisp ↔ CLI protocol
                      ▼
┌─────────────────────────────────────────────┐
│         asana-org-bridge (CLI)              │
│  ┌─────────────────────────────────────┐   │
│  │ Commands (Typer)                     │   │
│  │  - doctor, db-init, sync-pull/preview │   │
│  └─────────────────────────────────────┘   │
│  ┌─────────────────────────────────────┐   │
│  │ Sync Engine                         │   │
│  │  - Pull adapter                     │   │
│  │  - Diff engine                      │   │
│  │  - Mutation executor                │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│           SQLite Cache                      │
│  - tasks_snapshot                           │
│  - org_mirror_state                         │
│  - pending_mutations                        │
│  - sync_runs                                 │
└─────────────────────────────────────────────┘
```

## Security

- PAT tokens are loaded from environment or secure sources
- Logs redact PII by default (emails, tokens, URLs with secrets)
- No secrets persisted in Org files or database

## Troubleshooting

### No authentication token

```
⚠ No PAT configured (set ASANA_PAT env var)
  Sync commands will use mock data mode
```

Set `ASANA_PAT` environment variable to enable real API calls.

### Database not initialized

```
✗ Database not initialized (run 'db-init')
```

Run `asana-org-bridge db-init` first.

### Mock data mode

When running without a valid PAT, commands use deterministic mock data for testing.
