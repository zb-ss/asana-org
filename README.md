# Asana ↔ Org-mode Integration

A powerful, bidirectional synchronization tool between Asana and Emacs Org-mode.

## Overview

This project provides a robust bridge between Asana tasks and Org-mode files. Manage your Asana tasks directly from Emacs, leveraging Org-mode for scheduling, notes, and task management.

The system consists of:
1.  **`asana-org-bridge`**: A Python CLI handling the Asana REST API and local SQLite caching.
2.  **`asana-org.el`**: An Emacs package providing a rich interface for interacting with the bridge.

### Architecture

```
┌─────────────────────────────────────────┐
│           Emacs (asana-org)             │
├─────────────────────────────────────────┤
│  asana-org.el      - Main commands      │
│  asana-org-sync    - Bridge wrappers    │
└────────────────┬────────────────────────┘
                 │ CLI (JSON)
                 ▼
┌─────────────────────────────────────────┐
│         asana-org-bridge                │
│  - Asana REST API                       │
│  - SQLite cache & Conflict detection    │
└─────────────────────────────────────────┘
```

## Quickstart

### 1. Install the Bridge
```bash
cd bridge
pip install -e .
```

### 2. Install the Emacs Client
```elisp
(add-to-list 'load-path "/path/to/asana-org/elisp")
(require 'asana-org)
(asana-org-transient-setup-keybindings)
```

### 3. Configure & Sync
1.  **Set PAT**: `export ASANA_PAT="your_personal_access_token"`
2.  **Initialize**:
    ```bash
    asana-org-bridge doctor    # Verify setup
    asana-org-bridge db-init   # Initialize database
    ```
3.  **Map Projects**:
    ```elisp
    (setq asana-org-project-name-mapping '(("project_gid" . "~/org/asana/work.org")))
    ```
4.  **First Sync**: Run `M-x asana-org-sync-pull` in Emacs.

## Documentation

Detailed documentation is available in the `docs/` directory:

-   **[Installation](docs/installation.md)**: Detailed setup for Python and Emacs.
-   **[Configuration](docs/configuration.md)**: Environment variables and Emacs customization.
-   **[Usage Guide](docs/usage.md)**: How to use the CLI and Emacs interface.
-   **[Safety & Privacy](docs/safety.md)**: Conflict model and data handling.
-   **[Troubleshooting](docs/troubleshooting.md)**: Solutions to common issues.
-   **[Development](docs/development.md)**: Setup for contributors.
-   **[CLI Contract](docs/cli-contract.md)**: Technical details of the JSON bridge.

### Component Specifics
-   **[Bridge CLI](docs/bridge.md)**: Deep dive into the Python bridge.
-   **[Emacs Client](docs/emacs.md)**: Deep dive into the Elisp package.

## Core Commands

### CLI
-   `doctor`: Run diagnostics.
-   `db-init`: Initialize local database.
-   `sync-pull`: Fetch tasks from Asana.
-   `sync-preview`: Preview local changes.
-   `sync-apply`: Push changes to Asana.
-   `move-task`: Move task to another project/section.
-   `comment-append`: Add a comment to a task.

### Emacs (via `C-c a`)
-   `p`: **Pull** (`asana-org-sync-pull`)
-   `v`: **Preview** (`asana-org-sync-preview`)
-   `a`: **Apply** (`asana-org-sync-apply`)
-   `m`: **Move** (`asana-org-move-task`)
-   `c`: **Comment** (`asana-org-comment-append`)

## Release & Distribution

We follow [SemVer](https://semver.org/) and are working towards a stable 1.0 release.

- **GitHub**: Primary source for code and [releases](https://github.com/zb-ss/asana-org/releases).
- **PyPI**: Bridge CLI published automatically on GitHub Release. Install: `pipx install asana-org-bridge`.
- **MELPA**: Awaiting initial manual recipe submission. After that, package updates automatically from source.

See **[Release Process](docs/release.md)** for full details on publishing and distribution.

---
*License: GPL-3.0-or-later (Emacs) / MIT (Bridge)*
