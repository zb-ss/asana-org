# Usage Guide

Detailed instructions for using the Asana ↔ Org-mode integration.

## CLI Commands

The `asana-org-bridge` provides several commands for managing the synchronization process.

-   **`doctor`**: Run diagnostics to verify setup, Python version, and Asana connectivity.
-   **`db-init`**: Initialize or migrate the local SQLite database schema.
-   **`sync-pull`**: Fetch latest tasks from Asana and update the local cache.
-   **`sync-preview`**: Compare local Org changes with the cached state and Asana. Returns JSON for Emacs.
-   **`sync-apply`**: Push approved mutations back to Asana.
-   **`move-task <gid> --to <project_gid>`**: Move a task to a different project or section.
-   **`comment-append <gid> --body "..."`**: Append a comment to a specific task.

## Emacs Interface

The primary interface is the Transient menu, accessible via `C-c a` (if `asana-org-transient-setup-keybindings` is called).

### Keybindings in Transient Menu

-   `p`: **Pull** (`asana-org-sync-pull`) - Fetch latest state from Asana.
-   `v`: **Preview** (`asana-org-sync-preview`) - See what changes will be sent to Asana.
-   `a`: **Apply** (`asana-org-sync-apply`) - Push changes to Asana.
-   `m`: **Move** (`asana-org-move-task`) - Move task to another project/section.
-   `c`: **Comment** (`asana-org-comment-append`) - Append a comment to the current task.

### Working with Org Files

When tasks are synced to Org files, they are rendered as standard Org headings with specific properties.

-   **TODO State**: Changing the TODO state of a heading will be synced to Asana's completion status.
-   **Scheduled/Deadline**: Setting `SCHEDULED` or `DEADLINE` timestamps will update Asana's Start and Due dates.
-   **Properties**: Do not manually edit the `ASANA_GID` or other `ASANA_*` properties, as these are used for tracking.
-   **Comments**: You can view comments in the `ASANA_COMMENTS` drawer. To add a new comment, use the `comment-append` command.
