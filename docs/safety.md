# Safety & Data Privacy

To ensure data integrity and user privacy, `asana-org` follows a strict set of principles for synchronization and data handling.

## Conflict Model

To prevent accidental data loss, `asana-org` uses a "baseline" comparison model. This ensures that you never overwrite changes made in Asana by other users or via the web interface without seeing them first.

### How it works

1.  **Baseline**: When you run `sync-pull`, the current state of Asana tasks is saved as a "baseline" in your local SQLite database.
2.  **Local Changes**: You make changes to your Org files.
3.  **Preview**: When you run `sync-preview`, the tool compares:
    -   Your current Org file state.
    -   The local baseline (last known state from Asana).
    -   The current remote state in Asana.
4.  **Blocking Conflicts**: If a field (e.g., Due Date) was changed in Asana *after* your last sync (i.e., the remote state differs from the baseline), and you also changed that field locally, the change will be **blocked** during preview.

### Resolution

If a sync is blocked due to a conflict:
1.  Run `sync-pull` to fetch the latest remote state and update your local baseline.
2.  Resolve any discrepancies in your Org file manually.
3.  Re-run the `sync-preview` and `sync-apply` cycle.

## Write-back Fields

Only specific fields are synced back to Asana to ensure stability and prevent accidental corruption of complex Asana task data:

-   **TODO state**: Mapped to Asana completion status.
-   **SCHEDULED**: Mapped to Asana Start Date.
-   **DEADLINE**: Mapped to Asana Due Date.
-   **Comments**: Append-only. Local edits to existing comments in Org are not synced back to Asana; only new comments added to the task are pushed.

## Privacy & Security

### No Secrets in Org

Your Asana Personal Access Token (PAT) is never stored in Org files or the local SQLite database. It is read from environment variables or a `.env` file at runtime.

### Redacted Logs

The bridge CLI is designed to be "safe for sharing" logs. It automatically redacts PII (Personally Identifiable Information) from logs by default, including:
-   Email addresses
-   Access tokens
-   URLs containing secrets

This behavior can be toggled via the `ASANA_ORG_LOG_REDACT_PII` environment variable, but it is recommended to keep it enabled.

### Local Cache

Task data is cached locally in a SQLite database (`~/.local/share/asana-org/bridge.db` by default). This cache serves three purposes:
1.  **Performance**: Minimizes expensive API calls to Asana.
2.  **Offline Diffing**: Allows the tool to detect what you've changed in Org even when offline.
3.  **Conflict Detection**: Stores the baseline state required for the conflict model.
