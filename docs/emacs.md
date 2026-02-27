# Asana ↔ Org-mode Integration (Emacs)

This document describes the Emacs client for the Asana ↔ Org-mode integration.

## Overview

The `asana-org` Emacs package provides bidirectional synchronization between Asana and Org mode files. It pulls tasks from Asana into Org files and provides commands for syncing changes back to Asana.

## Installation

### Requirements

- Emacs 28.1+
- Transient 0.4.0+ (for menus)
- `asana-org-bridge` CLI installed and in PATH

### Manual Installation

```elisp
(add-to-list 'load-path "/path/to/elisp")
(require 'asana-org)

;; Optional: Set custom variables before activation
(setq asana-org-bridge-binary "asana-org-bridge")
(setq asana-org-root-directory "~/org/asana")

;; Setup keybindings
(asana-org-transient-setup-keybindings)
```

### With Doom Emacs

```elisp
;; packages.el
(package! asana-org)

;; config.el
(require 'asana-org)
(asana-org-transient-setup-keybindings)
```

## Configuration

Customize the following variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `asana-org-bridge-binary` | `"asana-org-bridge"` | Path to bridge executable |
| `asana-org-root-directory` | `"~/org/asana"` | Root directory for Org files |
| `asana-org-cache-directory` | `~/.emacs.d/.asana-org-cache` | Cache storage |
| `asana-org-confirm-threshold` | `5` | Changes requiring confirmation |
| `asana-org-batch-size` | `20` | Max mutations per apply |
| `asana-org-dry-run` | `t` | Enable dry-run for first use |

### Per-Project File Mapping

```elisp
(setq asana-org-project-name-mapping
      '(("project_gid_123" . "~/org/asana/work.org")
        ("project_gid_456" . "~/org/asana/personal.org")))
```

## Usage

### Transient Menu

Access all commands via `C-c a`:

```
C-c a         → Open main menu
C-c a p       → Pull tasks
C-c a v       → Preview changes  
C-c a a       → Apply changes
C-c a m       → Move task
C-c a c       → Add comment
```

### Main Commands

#### Pull Tasks

```elisp
M-x asana-org-sync-pull
```

Pulls your My Tasks from Asana and creates/updates Org files in the root directory.

#### Preview Changes

```elisp
M-x asana-org-sync-preview
```

Shows pending outbound changes, conflicts, and warnings in a preview buffer.

#### Apply Changes

```elisp
M-x asana-org-sync-apply
```

Applies approved changes to Asana. Requires preview to be run first.

#### Move Task

```elisp
M-x asana-org-move-task
```

Moves a task to a different project/section. Prompts for task GID, target project, and optional section.

#### Add Comment

```elisp
M-x asana-org-comment-append
```

Appends a comment to a task. Comments are append-only.

## Org Properties

Tasks synced from Asana include these properties:

| Property | Description |
|----------|-------------|
| `ASANA_GID` | Asana task GID (unique identifier) |
| `ASANA_PERMALINK` | URL to task in Asana web |
| `ASANA_REMOTE_MODIFIED_AT` | Last modified timestamp |
| `ASANA_LOCAL_HASH` | Local content hash for change detection |
| `ASANA_PROJECT_GID` | Current project GID |
| `ASANA_SECTION_GID` | Current section GID |

### Comments Drawer

Task comments are stored in the `ASANA_COMMENTS` drawer:

```org
:ASANA_COMMENTS:
- [2025-01-15] john@example.com: Started working on this
- [2025-01-16] john@example.com: Blocked by dependency
:END:
```

## Sync Contract

### Write-back Fields

Only these fields can be written back to Asana:

- TODO/DONE state
- Start date (`SCHEDULED`)
- Due date (`DEADLINE`)
- Project membership (via move command)
- Section membership (via move command)
- Comments (append-only)

### Conflict Policy

- Remote changes to mapped fields since baseline → **BLOCK**
- Remote changes to unmapped fields → Allow with warning
- Missing GID or permalink mismatch → **BLOCK**

### Confirmation Requirements

- Changes > `asana-org-confirm-threshold` → Confirm required
- Any move operation → Confirm required

## Troubleshooting

### Bridge Not Found

```
Error: Bridge binary 'asana-org-bridge' not found
```

**Solution:** Ensure `asana-org-bridge` is installed and in your PATH, or customize `asana-org-bridge-binary`.

### Sync Failed

Check the log buffer `*Asana Org*` for detailed error messages.

### Conflicts

Run `asana-org-sync-pull` to fetch latest remote state, then re-run preview.

## Architecture

```
┌─────────────────────────────────────────┐
│           Emacs (asana-org)             │
├─────────────────────────────────────────┤
│  asana-org.el      - Main commands      │
│  asana-org-render  - Org rendering      │
│  asana-org-sync    - Bridge wrappers    │
│  asana-org-transient - Transient menus  │
└────────────────┬────────────────────────┘
                 │ CLI (JSON)
                 ▼
┌─────────────────────────────────────────┐
│         asana-org-bridge                │
│  - Asana REST API                       │
│  - MCP integration                      │
│  - SQLite cache                         │
└─────────────────────────────────────────┘
```

## License

GPL-3.0-or-later - See LICENSE file
