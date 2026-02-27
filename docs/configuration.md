# Configuration Reference

Comprehensive list of configuration options for both the Bridge CLI and the Emacs client.

## Bridge CLI Configuration

The bridge is configured primarily through environment variables. You can also use a `.env` file in the project root.

| Variable | Description | Default |
|----------|-------------|---------|
| `ASANA_PAT` | **Required** Asana Personal Access Token | None |
| `ASANA_ORG_DB_PATH` | Path to SQLite database | `~/.local/share/asana-org/bridge.db` |
| `ASANA_ORG_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `ASANA_ORG_LOG_REDACT_PII` | Redact PII (emails, tokens) from logs | `true` |
| `ASANA_ORG_MOCK_DATA` | Use mock data instead of real API calls | `false` |

## Emacs Customization

You can customize these variables using `M-x customize-group RET asana-org RET` or by setting them in your init file.

| Variable | Description | Default |
|----------|-------------|---------|
| `asana-org-bridge-binary` | Path to bridge executable | `"asana-org-bridge"` |
| `asana-org-root-directory` | Root directory for Org files | `"~/org/asana"` |
| `asana-org-project-name-mapping` | Alist mapping project GIDs to files | `nil` |
| `asana-org-confirm-threshold` | Number of changes requiring confirmation | `5` |
| `asana-org-dry-run` | Enable dry-run mode for sync-apply | `t` |

### Project Mapping Example

The `asana-org-project-name-mapping` is crucial for determining where tasks from different Asana projects are stored.

```elisp
(setq asana-org-project-name-mapping
      '(("1234567890123456" . "~/org/asana/work.org")
        ("9876543210987654" . "~/org/asana/personal.org")))
```
