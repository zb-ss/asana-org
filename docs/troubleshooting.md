# Troubleshooting

Common issues and their solutions for the Asana ↔ Org-mode integration.

## Bridge CLI Issues

### Bridge Not Found
**Symptoms:** Emacs reports `Error: Bridge binary 'asana-org-bridge' not found`.

**Solutions:**
1.  Ensure the bridge is installed: `pip install -e ./bridge`.
2.  Verify it's in your `PATH`: Run `asana-org-bridge --help` in a terminal.
3.  If it's installed but not in `PATH`, set the absolute path in Emacs:
    ```elisp
    (setq asana-org-bridge-binary "/path/to/your/venv/bin/asana-org-bridge")
    ```

### Authentication Errors
**Symptoms:** `401 Unauthorized` errors or "No PAT configured" warnings.

**Solutions:**
1.  Verify `ASANA_PAT` is correctly set in your environment.
2.  If using a `.env` file, ensure it's in the directory where you run the bridge or in the project root.
3.  Check if your token has expired in the [Asana Developer Console](https://app.asana.com/0/developer-console).

### Database Errors
**Symptoms:** `✗ Database not initialized` or schema errors.

**Solution:** Run the initialization command:
```bash
asana-org-bridge db-init
```

## Emacs Sync Issues

### Sync Conflicts
**Symptoms:** `sync-preview` shows blocked changes with a conflict reason.

**Solution:**
1.  Run `M-x asana-org-sync-pull` (or `p` in the transient menu) to refresh the local baseline.
2.  Check your Org file for changes that might conflict with recent remote updates.
3.  Re-run the preview.

### Missing Tasks
**Symptoms:** Tasks you expect to see are not appearing in your Org files.

**Solutions:**
1.  Verify your `asana-org-project-name-mapping` includes the GID of the project containing those tasks.
2.  Ensure the tasks are assigned to you (if syncing "My Tasks").
3.  Check the `*Asana Org*` buffer for any filtering or limit warnings.

## Getting Help

-   **Logs**: Check the `*Asana Org*` buffer in Emacs for the full output of the bridge commands.
-   **Debug Mode**: Set `ASANA_ORG_LOG_LEVEL=DEBUG` in your environment to get more verbose output from the bridge.
-   **GitHub Issues**: Search existing issues or open a new one on the GitHub repository.
