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

## Prerequisites

-   **Python 3.11+** (`python3 --version` to check)
-   **Emacs 28.1+** with `transient` 0.4.0+ (included in Doom Emacs and recent Emacs)
-   **Asana Personal Access Token** - generate one at:
    [Asana Developer Console](https://app.asana.com/0/developer-console) > Create new token

## Quickstart

### 1. Install the Bridge

```bash
git clone https://github.com/zb-ss/asana-org.git
cd asana-org/bridge

# Using a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Symlink the binary so Emacs can find it
ln -sf "$(pwd)/.venv/bin/asana-org-bridge" ~/.local/bin/asana-org-bridge
```

> **Note**: Ensure `~/.local/bin` is in your `PATH`. Add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile if needed.

Alternatively, once the bridge is published to PyPI (see [Release & Distribution](#release--distribution)), install globally with [pipx](https://pypa.github.io/pipx/):
```bash
pipx install asana-org-bridge
```

### 2. Configure Asana PAT

Add your Personal Access Token to your shell profile (`~/.bashrc` or `~/.zshrc`) so it persists across sessions:

```bash
echo 'export ASANA_PAT="your_personal_access_token"' >> ~/.bashrc
source ~/.bashrc
```

### 3. Initialize the Database

```bash
asana-org-bridge doctor    # Verify setup
asana-org-bridge db-init   # Initialize local SQLite cache
```

### 4. Install the Emacs Client

#### Doom Emacs

```elisp
;; in packages.el
(package! asana-org
  :recipe (:host github :repo "zb-ss/asana-org" :files ("elisp/*.el")))
```

```elisp
;; in config.el
(use-package! asana-org
  :defer t
  :commands (asana-org-transient asana-org-sync-pull asana-org-sync-preview
             asana-org-sync-apply asana-org-move-task asana-org-comment-append)
  :init
  (setq asana-org-bridge-binary "asana-org-bridge"
        asana-org-root-directory (expand-file-name "~/org/asana")
        asana-org-dry-run t)
  :config
  (asana-org-transient-setup-keybindings))
```

Then run `doom sync` and restart Emacs.

#### Vanilla Emacs / Spacemacs

```elisp
(add-to-list 'load-path "/path/to/asana-org/elisp")
(require 'asana-org)
(setq asana-org-bridge-binary "asana-org-bridge"
      asana-org-root-directory (expand-file-name "~/org/asana"))
(asana-org-transient-setup-keybindings)
```

### 5. Map Projects (Optional)

Map Asana project GIDs to human-readable Org file paths. Without mappings, tasks are stored by GID (e.g. `1234567890.org`).

```elisp
(setq asana-org-project-name-mapping
      '(("1234567890123456" . "~/org/asana/work.org")
        ("9876543210987654" . "~/org/asana/personal.org")))
```

> **Tip**: Find project GIDs from the Asana URL: `https://app.asana.com/0/<PROJECT_GID>/...`

### 6. First Sync

Run `M-x asana-org-sync-pull` in Emacs (or `C-c a p`).

The `~/org/asana/` directory is created automatically on first pull.

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
-   `sync-pull`: Fetch tasks from Asana (`--include-comments` for stories).
-   `sync-preview`: Preview local changes.
-   `sync-apply`: Push changes to Asana (with conflict detection).
-   `detect-changes`: Detect org file changes against cached snapshots.
-   `move-task`: Move task to another project/section (with section validation).
-   `comment-append`: Add a comment to a task.
-   `cache-prune`: Prune old cache entries per retention policy.
-   `status`: Show sync health diagnostics.
-   `validate`: Validate org task states against cached snapshots (reports mismatches and orphans).
-   `reconcile`: Reconcile local snapshots against current remote Asana state.
-   `rebuild-cache`: Rebuild the local snapshot cache from scratch.
-   `relink`: Update the stored permalink URL for a task.
-   `ai-summary`: Generate an AI summary for one or more tasks (optional, requires AI config).

### Emacs (via `C-c a`)
-   `p`: **Pull** (`asana-org-sync-pull`)
-   `d`: **Detect changes** (`asana-org-sync-detect-changes`)
-   `v`: **Preview** (`asana-org-sync-preview`)
-   `a`: **Apply** (`asana-org-sync-apply`)
-   `m`: **Move** (`asana-org-move-task`) — auto-refiles heading after success
-   `c`: **Comment** (`asana-org-comment-append`)
-   `s`: **Status** (`asana-org-sync-status`)

## Release & Distribution

We follow [SemVer](https://semver.org/) and are working towards a stable 1.0 release.

- **GitHub**: Primary source for code and [releases](https://github.com/zb-ss/asana-org/releases).
- **PyPI**: Not yet published. A GitHub Release will publish the bridge CLI to PyPI automatically (workflow configured); after the first release, install via `pipx install asana-org-bridge`.
- **MELPA**: Awaiting initial manual recipe submission. After that, the package updates automatically from source.

See **[Release Process](docs/release.md)** for full details on publishing and distribution.

---
*License: GPL-3.0-or-later (Emacs) / MIT (Bridge)*
