# Bridge CLI ↔ Emacs JSON Contract v1

## 1. Commands and Purpose

| Command | Purpose |
|---------|---------|
| `sync-preview` | Fetch pending changes, return diff-ready JSON for Emacs to display |
| `sync-apply` | Execute batch of mutations with idempotency; returns results/errors per mutation |
| `move-task` | Move a task to a different list/section |
| `comment-append` | Add a comment to a task |

**Note**: Commands use hyphenated naming (e.g., `sync-preview`, `sync-apply`).

`sync-apply` accepts JSON over stdin/stdout (`--json -`).
`move-task` and `comment-append` use CLI arguments for input, with optional JSON output (`--json`).

---

## 2. sync-preview Output Schema

### Schema

```json
{
  "version": "1",
  "command": "sync-preview",
  "status": "success",
  "data": {
    "pending_changes": [
      {
        "id": "string",
        "type": "task_move" | "comment_add" | "status_change" | "date_change",
        "description": "human-readable change summary",
        "current_state": { /* optional, for conflicts */ },
        "proposed_state": { /* the change */ }
      }
    ]
  },
  "summary": {
    "total": 0,
    "status_changes": 0,
    "date_changes": 0,
    "comments": 0,
    "moves": 0
  }
}
```

### Example: Clean (No Conflicts)

```json
{
  "version": "1",
  "command": "sync-preview",
  "status": "success",
  "data": {
    "pending_changes": [
      {
        "id": "pc_001",
        "type": "task_move",
        "description": "Move \"Fix login bug\" from Backlog to In Progress",
        "proposed_state": {
          "task_gid": "1234567890",
          "task_name": "Fix login bug",
          "from_list": "Backlog",
          "to_list": "In Progress"
        }
      },
      {
        "id": "pc_002",
        "type": "comment_add",
        "description": "Add comment to \"Fix login bug\"",
        "proposed_state": {
          "task_gid": "1234567890",
          "text": "Started working on this. Will push a fix by EOD."
        }
      }
    ]
  }
}
```

### Example: Conflict Detected

```json
{
  "version": "1",
  "command": "sync-preview",
  "status": "success",
  "data": {
    "pending_changes": [
      {
        "id": "pc_001",
        "type": "task_move",
        "description": "Move \"Fix login bug\" from Backlog to In Progress",
        "current_state": {
          "task_gid": "1234567890",
          "task_name": "Fix login bug",
          "current_list": "Done"
        },
        "proposed_state": {
          "task_gid": "1234567890",
          "task_name": "Fix login bug",
          "from_list": "Backlog",
          "to_list": "In Progress"
        },
        "conflict": {
          "detected": true,
          "reason": "Task already moved to 'Done' by another user",
          "blocking": true
        }
      }
    ]
  }
}
```

---

## 3. sync-apply Request/Response

### Request Schema

```json
{
  "version": "1",
  "command": "sync-apply",
  "idempotency_key": "unique-request-key-uuid",
  "mutations": [
    {
      "idempotency_key": "unique-mutation-key-uuid",
      "type": "task_move" | "comment_add" | "status_change" | "date_change",
      "payload": { /* mutation-specific */ }
    }
  ]
}
```

### Request Example

```json
{
  "version": "1",
  "command": "sync-apply",
  "idempotency_key": "req_abc123_def456",
  "mutations": [
    {
      "idempotency_key": "mut_001",
      "type": "task_move",
      "payload": {
        "task_gid": "1234567890",
        "from_list": "Backlog",
        "to_list": "In Progress"
      }
    },
    {
      "idempotency_key": "mut_002",
      "type": "comment_add",
      "payload": {
        "task_gid": "1234567890",
        "text": "Started working on this."
      }
    }
  ]
}
```

### Response Schema

```json
{
  "version": "1",
  "command": "sync-apply",
  "status": "success" | "partial",
  "data": {
    "results": [
      {
        "idempotency_key": "string",
        "status": "applied" | "conflict" | "error",
        "details": { /* optional */ }
      }
    ],
    "summary": {
      "total": 0,
      "applied": 0,
      "failed": 0
    }
  }
}
```

### Response Example

```json
{
  "version": "1",
  "command": "sync-apply",
  "status": "success",
  "data": {
    "results": [
      {
        "idempotency_key": "mut_001",
        "status": "applied",
        "details": {
          "action": "task_move",
          "task_gid": "1234567890",
          "new_list": "In Progress"
        }
      },
      {
        "idempotency_key": "mut_002",
        "status": "applied",
        "details": {
          "action": "comment_add",
          "comment_gid": "9876543210"
        }
      }
    ],
    "summary": {
      "total": 2,
      "applied": 2,
      "failed": 0
    }
  }
}
```

---

## 4. move-task Input/Response

### CLI Input

```bash
asana-org-bridge move-task 1234567890 --from "Backlog" --to "In Progress" --idempotency-key mut_move_xyz789 --json
```

### JSON Response (`--json`)

```json
{
  "version": "1",
  "command": "move-task",
  "status": "success",
  "data": {
    "result": {
      "idempotency_key": "mut_move_xyz789",
      "status": "applied",
      "task_gid": "1234567890",
      "new_list": "In Progress"
    }
  }
}
```

---

## 5. comment-append Input/Response

### CLI Input

```bash
asana-org-bridge comment-append 1234567890 --body "This is a comment appended from the CLI bridge." --idempotency-key mut_comment_abc123 --json
```

### JSON Response (`--json`)

```json
{
  "version": "1",
  "command": "comment-append",
  "status": "success",
  "data": {
    "result": {
      "idempotency_key": "mut_comment_abc123",
      "status": "applied",
      "comment_gid": "9876543210",
      "task_gid": "1234567890"
    }
  }
}
```

---

## 6. Standard Error Envelope

All error responses follow this schema:

```json
{
  "version": "1",
  "command": "<command-name>",
  "status": "error",
  "error": {
    "code": "string",
    "message": "human-readable description",
    "details": { /* optional context */ }
  }
}
```

### Error Codes

| Code | Meaning |
|------|---------|
| `INVALID_REQUEST` | Malformed JSON or missing required fields |
| `NOT_FOUND` | Referenced task/list not found |
| `CONFLICT` | Resource changed since preview |
| `RATE_LIMITED` | Too many requests |
| `AUTH_ERROR` | Authentication failed |
| `INTERNAL_ERROR` | Unexpected server error |

### Error Example

```json
{
  "version": "1",
  "command": "sync-apply",
  "status": "error",
  "error": {
    "code": "CONFLICT",
    "message": "Task 1234567890 was modified by another user",
    "details": {
      "idempotency_key": "mut_001",
      "server_version": "1699999999",
      "conflicting_field": "list"
    }
  }
}
```

---

## 7. Conflict Blocking Requirements

For a mutation to be considered **blocking** (requiring user intervention):

1. **Current state must be included** in `sync preview` response
2. **Conflict flag must be set**: `"conflict": { "detected": true, "blocking": true }`
3. **Conflict reason must be present**: brief explanation of what changed

Mutations **without** `current_state` in preview are treated as non-blocking—the CLI may optimistically apply them and report results.

**Idempotency key usage**:
- Top-level `idempotency_key` for the entire request
- Each mutation carries its own `idempotency_key` for deduplication
- Clients should reuse keys on retry to ensure exactly-once semantics
