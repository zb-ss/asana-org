"""CLI commands for Asana Org Bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from asana_org_bridge.auth import get_auth_manager
from asana_org_bridge.config import get_settings
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.logging_config import get_logger
from asana_org_bridge.sync import SyncEngine

app = typer.Typer(
    name="asana-org-bridge",
    help="Local bridge CLI for Asana ↔ Org-mode integration",
    add_completion=False,
)

console = Console()
logger = get_logger(__name__)


def get_database() -> Database:
    """Get database instance from settings."""
    settings = get_settings()
    return Database(
        db_path=settings.database.db_path,
        echo=settings.database.echo_sql,
    )


def get_sync_engine(use_mock: bool = False) -> SyncEngine:
    """Get a sync engine instance."""
    settings = get_settings()
    db = get_database()
    auth = get_auth_manager()

    actual_mock = use_mock or settings.sync.mock_data or not auth.get_pat()
    return SyncEngine(
        db=db,
        auth_manager=auth,
        use_mock=actual_mock,
    )


def build_error_envelope(
    command: str, code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a standard error envelope.

    Args:
        command: Command name
        code: Error code
        message: Human-readable message
        details: Optional details

    Returns:
        Error envelope dictionary
    """
    envelope: dict[str, Any] = {
        "version": "1",
        "command": command,
        "status": "error",
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details:
        envelope["error"]["details"] = details
    return envelope


def _validate_json_envelope(
    data: dict[str, Any],
    command_name: str,
    require_tasks: bool = True,
) -> list[dict[str, Any]] | None:
    """Validate a v1 JSON envelope and return the tasks list.

    Prints an error envelope and raises typer.Exit on validation failure.

    Args:
        data: Parsed JSON dict
        command_name: Expected command name for envelope checks
        require_tasks: Whether to require a 'tasks' array

    Returns:
        The tasks list on success, or None if not required
    """
    if not isinstance(data, dict):
        error_envelope = build_error_envelope(
            command=command_name,
            code="INVALID_REQUEST",
            message="Input must be a JSON object",
        )
        print(json.dumps(error_envelope, indent=2))
        raise typer.Exit(code=1)

    version = data.get("version")
    if version != "1":
        error_envelope = build_error_envelope(
            command=command_name,
            code="INVALID_REQUEST",
            message=f"Unsupported version '{version}': expected '1'",
        )
        print(json.dumps(error_envelope, indent=2))
        raise typer.Exit(code=1)

    command = data.get("command")
    if command != command_name:
        error_envelope = build_error_envelope(
            command=command_name,
            code="INVALID_REQUEST",
            message=f"Invalid command '{command}': expected '{command_name}'",
        )
        print(json.dumps(error_envelope, indent=2))
        raise typer.Exit(code=1)

    if require_tasks:
        tasks_list = data.get("tasks")
        if not isinstance(tasks_list, list):
            error_envelope = build_error_envelope(
                command=command_name,
                code="INVALID_REQUEST",
                message="Missing or invalid 'tasks' array",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)
        return tasks_list

    return None


def _read_json_input(json_input: str, command_name: str) -> dict[str, Any] | None:
    """Read and parse JSON from stdin ('-') or a file path.

    Args:
        json_input: Either '-' for stdin or a file path
        command_name: Command name for error envelopes

    Returns:
        Parsed JSON dict, or None if input was empty

    Raises:
        typer.Exit: On parse errors (after printing error envelope)
    """
    if json_input == "-":
        input_data = sys.stdin.read()
        if not input_data.strip():
            return None
        try:
            return json.loads(input_data)  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            error_envelope = build_error_envelope(
                command=command_name,
                code="INVALID_REQUEST",
                message=f"Invalid JSON input: {e}",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1) from None

    input_path = Path(json_input)
    if not input_path.exists():
        error_envelope = build_error_envelope(
            command=command_name,
            code="NOT_FOUND",
            message=f"Input file not found: {json_input}",
        )
        print(json.dumps(error_envelope, indent=2))
        raise typer.Exit(code=1)
    try:
        with open(input_path) as f:
            return json.load(f)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        error_envelope = build_error_envelope(
            command=command_name,
            code="INVALID_REQUEST",
            message=f"Invalid JSON in file {json_input}: {e}",
        )
        print(json.dumps(error_envelope, indent=2))
        raise typer.Exit(code=1) from None
    except Exception as e:
        error_envelope = build_error_envelope(
            command=command_name,
            code="INTERNAL_ERROR",
            message=f"Failed to read input file: {e}",
        )
        print(json.dumps(error_envelope, indent=2))
        raise typer.Exit(code=1) from None


@app.command()
def doctor() -> None:
    """Run diagnostics to verify bridge setup."""
    console.print("\n[bold]Asana Org Bridge - Diagnostics[/bold]\n")

    all_ok = True
    has_db = False
    has_pat = False

    # Check Python version
    console.print("[cyan]Python:[/cyan]")
    py_version = sys.version_info
    console.print(
        f"  Version: {py_version.major}.{py_version.minor}.{py_version.micro}"
    )
    if py_version >= (3, 11):
        console.print("  ✓ Python version OK")
    else:
        console.print("  ✗ Python 3.11+ required")
        all_ok = False

    # Check configuration
    console.print("\n[cyan]Configuration:[/cyan]")
    try:
        settings = get_settings()
        console.print(f"  Database: {settings.database.db_path}")
        console.print(f"  Auth source: {settings.auth.auth_source}")
        console.print(f"  Workspace GID: {settings.sync.workspace_gid or '(not set)'}")
        console.print(f"  Mock data mode: {settings.sync.mock_data}")
        console.print(f"  Log level: {settings.logging.level}")
        console.print("  ✓ Configuration loaded")
    except Exception as e:
        console.print(f"  ✗ Configuration error: {e}")
        all_ok = False

    # Check database
    console.print("\n[cyan]Database:[/cyan]")
    try:
        db = get_database()
        schema_version = db.get_schema_version()
        if schema_version:
            has_db = True
            console.print(f"  Schema version: {schema_version}")
            console.print("  ✓ Database initialized")
        else:
            console.print("  ✗ Database not initialized (run 'db init')")
            all_ok = False
    except Exception as e:
        console.print(f"  ✗ Database error: {e}")
        all_ok = False

    # Check authentication
    console.print("\n[cyan]Authentication:[/cyan]")
    try:
        auth = get_auth_manager()
        pat = auth.get_pat()
        if pat:
            has_pat = True
            console.print("  ✓ PAT loaded (secret)")
        else:
            console.print("  ⚠ No PAT configured (set ASANA_PAT env var)")
            console.print("    Sync commands will use mock data mode")
    except Exception as e:
        console.print(f"  ✗ Auth error: {e}")
        all_ok = False

    # Check directories
    console.print("\n[cyan]Directories:[/cyan]")
    try:
        settings = get_settings()
        db_dir = settings.database.db_path.parent
        if db_dir.exists():
            console.print(f"  ✓ Data directory exists: {db_dir}")
        else:
            console.print(f"  ✓ Data directory will be created: {db_dir}")
    except Exception as e:
        console.print(f"  ✗ Directory check error: {e}")

    # First-run and partial setup guidance
    if not has_db and not has_pat:
        # Full first-run: neither DB nor PAT
        console.print("\n[bold cyan]🚀 First-time Setup Guide[/bold cyan]\n")
        console.print("  1. Set your Asana PAT:")
        console.print('     [green]export ASANA_PAT="your_personal_access_token"[/green]')
        console.print("     (Get one at: https://app.asana.com/0/developer-console)\n")
        console.print("  2. Initialize the database:")
        console.print("     [green]asana-org-bridge db-init[/green]\n")
        console.print("  3. Pull your tasks:")
        console.print("     [green]asana-org-bridge sync-pull --json --incomplete-only[/green]\n")
        console.print("  4. In Emacs, run: [green]M-x asana-org-sync-pull[/green]")
    elif has_db and not has_pat:
        # Partial: DB exists but no PAT
        console.print("\n[bold yellow]⚠ Partial Setup: Missing PAT[/bold yellow]\n")
        console.print("  Database is initialized, but no Asana PAT is configured.")
        console.print("  Set your PAT to connect to Asana:\n")
        console.print('     [green]export ASANA_PAT="your_personal_access_token"[/green]')
        console.print("     (Get one at: https://app.asana.com/0/developer-console)\n")
        console.print("  Without a PAT, sync commands will use mock data mode.")
    elif not has_db and has_pat:
        # Partial: PAT exists but no DB
        console.print("\n[bold yellow]⚠ Partial Setup: Database Not Initialized[/bold yellow]\n")
        console.print("  PAT is configured, but the database has not been initialized.")
        console.print("  Run the following to set up the database:\n")
        console.print("     [green]asana-org-bridge db-init[/green]\n")
        console.print("  Then pull your tasks:")
        console.print("     [green]asana-org-bridge sync-pull --json --incomplete-only[/green]")

    # Summary
    console.print("\n[bold]Summary:[/bold]")
    if all_ok:
        console.print("  ✓ All checks passed")
    else:
        console.print("  ⚠ Some checks failed - review above")

    console.print()


@app.command()
def db_init() -> None:
    """Initialize the database with schema migrations."""
    console.print("[bold]Initializing database...[/bold]\n")

    try:
        db = get_database()
        migrator = MigrationManager(db)

        if not migrator.needs_init():
            console.print("Database already initialized.")
            version = db.get_schema_version()
            console.print(f"Current schema version: {version}")
            return

        # Apply pending migrations
        applied = migrator.migrate()

        if applied:
            console.print(f"Applied migrations: {', '.join(applied)}")
        else:
            console.print("No migrations to apply.")

        console.print(f"\n✓ Database initialized at: {db.db_path}")
        logger.info("database_initialized", db_path=str(db.db_path))

    except Exception as e:
        console.print(f"[red]Error initializing database: {e}[/red]")
        logger.error("database_init_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command("sync-pull")
def sync_pull(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force pull even if recently synced",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-l",
        help="Limit number of tasks to pull",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Filter tasks by project GID",
    ),
    include_comments: bool = typer.Option(
        False,
        "--include-comments",
        help="Include task comments/stories in pull results",
    ),
    incomplete_only: bool = typer.Option(
        False,
        "--incomplete-only",
        "-i",
        help="Only pull incomplete (not yet completed) tasks",
    ),
    modified_since: str | None = typer.Option(
        None,
        "--modified-since",
        help="Only pull tasks modified after this ISO date (e.g. 2025-01-01)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output JSON envelope",
    ),
) -> None:
    """Pull tasks from Asana and update local cache.

    Returns a JSON envelope with tasks array for Elisp consumption.
    """
    try:
        settings = get_settings()
        db = get_database()
        auth = get_auth_manager()

        # Check for mock mode
        use_mock = settings.sync.mock_data or not auth.get_pat()
        if use_mock and not json_output:
            console.print("[yellow]Running in mock data mode (no API calls)[/yellow]\n")

        # Initialize sync engine
        engine = SyncEngine(
            db=db,
            auth_manager=auth,
            use_mock=use_mock,
        )

        # Run pull
        result = engine.pull(
            force=force,
            limit=limit,
            incomplete_only=incomplete_only,
            modified_since=modified_since,
            include_comments=include_comments,
            project_gid=project,
        )

        if json_output:
            # Output JSON envelope with tasks array for Elisp compatibility
            # Include both the tasks array and summary stats
            response = {
                "version": "1",
                "command": "sync-pull",
                "status": "success",
                "data": {
                    "tasks": result.tasks,  # Full task array for Elisp
                    "sections": result.sections,  # project_gid -> ordered sections
                    "summary": {
                        "pulled": result.tasks_pulled,
                        "updated": result.tasks_updated,
                    },
                    "errors": result.errors if result.errors else [],
                },
            }
            print(json.dumps(response, indent=2))
            return

        # Display results
        console.print(f"Tasks pulled: {result.tasks_pulled}")
        console.print(f"Tasks updated: {result.tasks_updated}")

        if result.errors:
            console.print("\n[yellow]Warnings:[/yellow]")
            for error in result.errors:
                console.print(f"  - {error}")

        console.print("\n✓ Pull completed")

    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="sync-pull",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error during pull: {e}[/red]")
        logger.error("sync_pull_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command()
def sync_preview(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Show preview without applying",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output JSON contract format",
    ),
) -> None:
    """Preview pending changes before applying.

    Outputs structured JSON contract for Emacs to display.
    """
    try:
        engine = get_sync_engine()
        result = engine.preview(as_json=json_output)

        if json_output:
            # Output JSON contract format
            if result.preview_json:
                print(json.dumps(result.preview_json, indent=2))
            else:
                # No mutations - still output valid JSON
                print(
                    json.dumps(
                        {
                            "version": "1",
                            "command": "sync-preview",
                            "status": "success",
                            "data": {
                                "pending_changes": [],
                            },
                            "summary": {
                                "total": 0,
                                "status_changes": 0,
                                "date_changes": 0,
                                "comments": 0,
                                "moves": 0,
                            },
                        },
                        indent=2,
                    )
                )
            return

        # Rich console output
        console.print("[bold]Previewing pending changes...[/bold]\n")

        # Display pending mutations
        table = Table(title="Pending Mutations")
        table.add_column("ID", style="cyan")
        table.add_column("Task", style="white")
        table.add_column("Operation", style="yellow")
        table.add_column("Status", style="magenta")

        for mutation in result.mutations:
            table.add_row(
                str(mutation.id),
                mutation.task_gid[:12] + "...",
                mutation.operation,
                mutation.status,
            )

        console.print(table)
        console.print(f"\nTotal: {len(result.mutations)} pending mutations")

        if result.conflicts:
            console.print(f"\n[red]Conflicts detected: {len(result.conflicts)}[/red]")
            for conflict in result.conflicts:
                console.print(f"  - {conflict}")

        console.print("\n✓ Preview completed")

    except Exception as e:
        console.print(f"[red]Error during preview: {e}[/red]")
        logger.error("sync_preview_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command()
def sync_apply(
    confirm: bool = typer.Option(
        True,
        "--confirm/--yes",
        help="Require confirmation before applying",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be applied without applying",
    ),
    json_input: str | None = typer.Option(
        None,
        "--json",
        "-j",
        help="JSON input file or '-' for stdin",
    ),
) -> None:
    """Apply pending mutations to Asana.

    Accepts approved mutation set via JSON input file or stdin.
    Processes each with idempotency_key, returns per-mutation status.
    """
    try:
        engine = get_sync_engine()

        # Parse JSON input if provided
        mutations_json = None
        if json_input:
            mutations_json = _read_json_input(json_input, "sync-apply")

        # If no JSON input, use traditional apply
        if not mutations_json:
            result = engine.apply(dry_run=dry_run)

            console.print("[bold]Applying pending mutations...[/bold]\n")

            if engine.use_mock:
                console.print(
                    "[yellow]Running in mock data mode (no API calls)[/yellow]\n"
                )

            console.print(f"Mutations applied: {result.applied}")
            console.print(f"Mutations failed: {result.failed}")

            if result.errors:
                console.print("\n[yellow]Errors:[/yellow]")
                for error in result.errors:
                    console.print(f"  - {error}")

            console.print("\n✓ Apply completed")
        else:
            # JSON input mode - return JSON output
            result = engine.apply(dry_run=dry_run, mutations_json=mutations_json)

            # Always output JSON result
            if result.results_json:
                print(json.dumps(result.results_json, indent=2))
            else:
                # Error case
                if result.errors:
                    error_envelope = build_error_envelope(
                        command="sync-apply",
                        code="INVALID_REQUEST",
                        message="; ".join(result.errors),
                    )
                    print(json.dumps(error_envelope, indent=2))
                else:
                    print(
                        json.dumps(
                            {
                                "version": "1",
                                "command": "sync-apply",
                                "status": "success",
                                "data": {
                                    "results": [],
                                    "summary": {
                                        "total": 0,
                                        "applied": 0,
                                        "failed": 0,
                                    },
                                },
                            },
                            indent=2,
                        )
                    )

    except Exception as e:
        console.print(f"[red]Error during apply: {e}[/red]")
        logger.error("sync_apply_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command("move-task")
def move_task(
    task_gid: str = typer.Argument(..., help="Task GID to move"),
    from_list: str = typer.Option("", "--from", help="Source list/section"),
    to_list: str = typer.Option("", "--to", help="Destination list/section"),
    idempotency_key: str | None = typer.Option(
        None, "--idempotency-key", help="Optional idempotency key"
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output JSON envelope"
    ),
) -> None:
    """Move a task to a different project/section.

    Follows JSON contract from docs/cli-contract.md.
    """
    try:
        # Validate required parameters
        if not task_gid:
            error_envelope = build_error_envelope(
                command="move-task",
                code="INVALID_REQUEST",
                message="task_gid is required",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)

        if not to_list:
            error_envelope = build_error_envelope(
                command="move-task",
                code="INVALID_REQUEST",
                message="to_list (--to) is required",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)

        engine = get_sync_engine()
        response = engine.execute_move_task(
            task_gid=task_gid,
            from_list=from_list,
            to_list=to_list,
            idempotency_key=idempotency_key,
        )

        if json_output:
            print(json.dumps(response, indent=2))
        else:
            if response.get("status") == "error":
                error_obj = response.get("error", {})
                raise RuntimeError(
                    str(error_obj.get("message", "Unknown move-task error"))
                )

            console.print(f"[bold]Moving task {task_gid}[/bold]")
            console.print(f"From: {from_list or '(none)'}")
            console.print(f"To: {to_list}")
            console.print("\n✓ Task moved successfully")

    except typer.Exit:
        raise
    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="move-task",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error moving task: {e}[/red]")
            logger.error("move_task_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command("comment-append")
def comment_append(
    task_gid: str = typer.Argument(..., help="Task GID to comment on"),
    body: str = typer.Option("", "--body", help="Comment text"),
    idempotency_key: str | None = typer.Option(
        None, "--idempotency-key", help="Optional idempotency key"
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output JSON envelope"
    ),
) -> None:
    """Append a comment to a task.

    Follows JSON contract from docs/cli-contract.md.
    """
    try:
        # Validate required parameters
        if not task_gid:
            error_envelope = build_error_envelope(
                command="comment-append",
                code="INVALID_REQUEST",
                message="task_gid is required",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)

        if not body:
            error_envelope = build_error_envelope(
                command="comment-append",
                code="INVALID_REQUEST",
                message="body (--body) is required",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)

        engine = get_sync_engine()
        response = engine.execute_comment_append(
            task_gid=task_gid,
            text=body,
            idempotency_key=idempotency_key,
        )

        if json_output:
            print(json.dumps(response, indent=2))
        else:
            if response.get("status") == "error":
                error_obj = response.get("error", {})
                raise RuntimeError(
                    str(error_obj.get("message", "Unknown comment-append error"))
                )

            console.print(f"[bold]Adding comment to task {task_gid}[/bold]")
            console.print(f"Comment: {body[:50]}{'...' if len(body) > 50 else ''}")
            console.print("\n✓ Comment added successfully")

    except typer.Exit:
        raise
    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="comment-append",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error adding comment: {e}[/red]")
            logger.error("comment_append_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command("cache-prune")
def cache_prune(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Preview what would be pruned without deleting (default: dry-run)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output JSON envelope",
    ),
    report: bool = typer.Option(
        False,
        "--report",
        "-r",
        help="Show detailed report",
    ),
) -> None:
    """Prune old cache entries based on retention policy.

    Removes old task snapshots, sync run journals, and completed mutations
    according to configured retention periods. Always preserves the most
    recent snapshot per task and never deletes pending/failed mutations.
    """
    try:
        engine = get_sync_engine()
        result = engine.prune_cache(dry_run=dry_run)

        report_data = {
            "snapshots_deleted": result.snapshots_deleted,
            "sync_runs_deleted": result.sync_runs_deleted,
            "mutations_deleted": result.mutations_deleted,
            "dry_run": result.dry_run,
        }

        if json_output:
            response = {
                "version": "1",
                "command": "cache-prune",
                "status": "success",
                "data": {
                    "report": report_data,
                },
            }
            print(json.dumps(response, indent=2))
            return

        # Rich console output
        action = "Would prune" if dry_run else "Pruned"
        console.print(f"[bold]{action} cache entries:[/bold]\n")

        if report:
            settings = get_settings()
            table = Table(title="Cache Prune Report")
            table.add_column("Category", style="cyan")
            table.add_column("Count", style="yellow", justify="right")
            table.add_column("Retention", style="white")

            table.add_row(
                "Task snapshots",
                str(result.snapshots_deleted),
                f"{settings.sync.snapshot_retention_days} days",
            )
            table.add_row(
                "Sync runs",
                str(result.sync_runs_deleted),
                f"{settings.sync.journal_retention_days} days",
            )
            table.add_row(
                "Completed mutations",
                str(result.mutations_deleted),
                f"{settings.sync.audit_retention_days} days",
            )
            console.print(table)
        else:
            console.print(f"  Snapshots: {result.snapshots_deleted}")
            console.print(f"  Sync runs: {result.sync_runs_deleted}")
            console.print(f"  Mutations: {result.mutations_deleted}")

        if dry_run:
            console.print(
                "\n[yellow]Dry run - no data was deleted. "
                "Use --no-dry-run to delete.[/yellow]"
            )
        else:
            console.print("\n\u2713 Cache pruned successfully")

    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="cache-prune",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error during cache prune: {e}[/red]")
        logger.error("cache_prune_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command()
def status(
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output JSON envelope"
    ),
) -> None:
    """Show sync health status and diagnostics.

    Reports last pull/apply timestamps, snapshot counts, pending/failed
    mutations, and database metadata.
    """
    try:
        engine = get_sync_engine()
        sync_status = engine.get_status()

        if json_output:
            response = {
                "version": "1",
                "command": "status",
                "status": "success",
                "data": {
                    "sync_status": sync_status,
                },
            }
            print(json.dumps(response, indent=2))
            return

        # Rich console output
        console.print("\n[bold]Asana Org Bridge - Sync Status[/bold]\n")

        table = Table(title="Sync Health")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Last Pull", sync_status["last_pull_at"] or "(never)")
        table.add_row("Last Apply", sync_status["last_apply_at"] or "(never)")
        table.add_row("Snapshots", str(sync_status["snapshot_count"]))
        table.add_row("Unique Tasks", str(sync_status["unique_tasks"]))
        table.add_row("Pending Mutations", str(sync_status["pending_mutations"]))
        table.add_row("Failed Mutations", str(sync_status["failed_mutations"]))
        table.add_row("Total Sync Runs", str(sync_status["total_sync_runs"]))
        table.add_row(
            "Schema Version", sync_status["schema_version"] or "(not initialized)"
        )

        db_size = sync_status["db_size_bytes"]
        if db_size is not None:
            if db_size >= 1024 * 1024:
                size_str = f"{db_size / (1024 * 1024):.1f} MB"
            elif db_size >= 1024:
                size_str = f"{db_size / 1024:.1f} KB"
            else:
                size_str = f"{db_size} B"
            table.add_row("DB Size", size_str)
        else:
            table.add_row("DB Size", "(file not found)")

        table.add_row("DB Path", sync_status["db_path"])

        console.print(table)
        console.print()

    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="status",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error getting status: {e}[/red]")
        logger.error("status_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command("detect-changes")
def detect_changes(
    json_input: str | None = typer.Option(
        None,
        "--json",
        "-j",
        help="JSON input file or '-' for stdin",
    ),
) -> None:
    """Detect changes between org file state and cached snapshots.

    Accepts task states via JSON stdin and compares against the latest
    TaskSnapshot in the database.  Returns a list of pending changes
    that can be fed into sync-preview/sync-apply.
    """
    try:
        # Parse JSON input
        tasks_json: dict[str, Any] | None = None
        if json_input:
            tasks_json = _read_json_input(json_input, "detect-changes")

        if not tasks_json:
            error_envelope = build_error_envelope(
                command="detect-changes",
                code="INVALID_REQUEST",
                message="No JSON input provided. Use --json - for stdin.",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)

        tasks_list = _validate_json_envelope(tasks_json, "detect-changes")

        engine = get_sync_engine()
        result = engine.detect_changes(task_states=tasks_list)  # type: ignore[arg-type]

        response: dict[str, Any] = {
            "version": "1",
            "command": "detect-changes",
            "status": "success",
            "data": {
                "pending_changes": result.pending_changes,
                "summary": result.summary,
            },
        }
        if result.warnings:
            response["data"]["warnings"] = result.warnings

        print(json.dumps(response, indent=2))

    except typer.Exit:
        raise
    except Exception as e:
        error_envelope = build_error_envelope(
            command="detect-changes",
            code="INTERNAL_ERROR",
            message=str(e),
        )
        print(json.dumps(error_envelope, indent=2))
        logger.error("detect_changes_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command()
def relink(
    task_gid: str = typer.Argument(..., help="Task GID to relink"),
    permalink: str = typer.Option(
        ..., "--permalink", help="New permalink URL for the task"
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output JSON envelope"
    ),
) -> None:
    """Relink a task to a new Asana permalink URL.

    Updates the stored permalink_url for a task snapshot, useful when
    a task has been moved and its permalink has changed.
    """
    try:
        engine = get_sync_engine()
        result = engine.relink_task(task_gid=task_gid, new_permalink=permalink)

        if json_output:
            if "error" in result:
                # Engine returned an error dict
                error_envelope = build_error_envelope(
                    command="relink",
                    code=result.get("code", "INTERNAL_ERROR"),
                    message=result.get("error", "Unknown error"),
                )
                print(json.dumps(error_envelope, indent=2))
                raise typer.Exit(code=1)

            response = {
                "version": "1",
                "command": "relink",
                "status": "success",
                "data": result,
            }
            print(json.dumps(response, indent=2))
        else:
            if "error" in result:
                console.print(f"[red]Error: {result['error']}[/red]")
                raise typer.Exit(code=1)

            console.print(f"[bold]Relinked task {task_gid}[/bold]")
            console.print(f"  Old permalink: {result.get('old_permalink', '(none)')}")
            console.print(f"  New permalink: {result.get('new_permalink')}")
            console.print("\n✓ Task relinked successfully")

    except typer.Exit:
        raise
    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="relink",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error relinking task: {e}[/red]")
        logger.error("relink_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command()
def reconcile(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output JSON envelope",
    ),
) -> None:
    """Reconcile local snapshots against remote Asana state.

    Compares cached task snapshots with current remote data and reports
    any drift in key fields (completed, due_on, start_on, name).
    """
    try:
        engine = get_sync_engine()
        rec_result = engine.reconcile()

        if json_output:
            response = {
                "version": "1",
                "command": "reconcile",
                "status": "success",
                "data": {
                    "drifted_tasks": rec_result.drifted_tasks,
                    "missing_remote": rec_result.missing_remote,
                    "summary": rec_result.summary,
                },
            }
            print(json.dumps(response, indent=2))
            return

        # Rich console output
        console.print("[bold]Reconcile Results[/bold]\n")

        if rec_result.drifted_tasks:
            table = Table(title="Drifted Tasks")
            table.add_column("GID", style="cyan")
            table.add_column("Field", style="yellow")
            table.add_column("Snapshot", style="white")
            table.add_column("Remote", style="green")

            for drift in rec_result.drifted_tasks:
                table.add_row(
                    drift["gid"],
                    drift["field"],
                    str(drift["snapshot_value"]),
                    str(drift["remote_value"]),
                )
            console.print(table)
        else:
            console.print("  No drift detected")

        if rec_result.missing_remote:
            console.print(f"\n[yellow]Missing from remote: {len(rec_result.missing_remote)}[/yellow]")
            for gid in rec_result.missing_remote:
                console.print(f"  - {gid}")

        summary = rec_result.summary
        console.print(
            f"\nChecked: {summary['total_checked']}, "
            f"Drifted: {summary['drifted']}, "
            f"Missing: {summary['missing']}"
        )

    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="reconcile",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error during reconcile: {e}[/red]")
        logger.error("reconcile_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command("rebuild-cache")
def rebuild_cache(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output JSON envelope",
    ),
    confirm: bool = typer.Option(
        True,
        "--confirm/--no-confirm",
        help="Require confirmation before rebuilding (default: requires confirm)",
    ),
) -> None:
    """Rebuild the local snapshot cache from scratch.

    Deletes all cached task snapshots and re-fetches them from Asana
    (or re-inserts from mock data). Use --no-confirm to skip the
    safety prompt.
    """
    try:
        if confirm and not json_output:
            if not typer.confirm(
                "This will delete all cached snapshots and rebuild from remote. Continue?"
            ):
                console.print("Cancelled.")
                raise typer.Exit(code=0)

        engine = get_sync_engine()
        rb_result = engine.rebuild_cache()

        if json_output:
            response = {
                "version": "1",
                "command": "rebuild-cache",
                "status": "success",
                "data": {
                    "snapshots_deleted": rb_result.snapshots_deleted,
                    "snapshots_created": rb_result.snapshots_created,
                },
            }
            print(json.dumps(response, indent=2))
            return

        console.print("[bold]Cache Rebuild Complete[/bold]\n")
        console.print(f"  Snapshots deleted: {rb_result.snapshots_deleted}")
        console.print(f"  Snapshots created: {rb_result.snapshots_created}")
        console.print("\n\u2713 Cache rebuilt successfully")

    except typer.Exit:
        raise
    except Exception as e:
        if json_output:
            error_envelope = build_error_envelope(
                command="rebuild-cache",
                code="INTERNAL_ERROR",
                message=str(e),
            )
            print(json.dumps(error_envelope, indent=2))
        else:
            console.print(f"[red]Error during cache rebuild: {e}[/red]")
        logger.error("rebuild_cache_failed", error=str(e))
        raise typer.Exit(code=1) from None


@app.command()
def validate(
    json_input: str | None = typer.Option(
        None,
        "--json",
        "-j",
        help="JSON input file or '-' for stdin with org task states",
    ),
) -> None:
    """Validate org task states against cached snapshots.

    Accepts org task states via JSON stdin (or file) and compares them
    against the latest TaskSnapshot for each task. Reports mismatches,
    orphaned org tasks (no snapshot), and orphaned DB snapshots.
    """
    try:
        tasks_json: dict[str, Any] | None = None
        if json_input:
            tasks_json = _read_json_input(json_input, "validate")

        if not tasks_json:
            error_envelope = build_error_envelope(
                command="validate",
                code="INVALID_REQUEST",
                message="No JSON input provided. Use --json - for stdin.",
            )
            print(json.dumps(error_envelope, indent=2))
            raise typer.Exit(code=1)

        tasks_list = _validate_json_envelope(tasks_json, "validate")

        engine = get_sync_engine()
        val_result = engine.validate(org_task_states=tasks_list)  # type: ignore[arg-type]

        response: dict[str, Any] = {
            "version": "1",
            "command": "validate",
            "status": "success",
            "data": {
                "mismatches": val_result.mismatches,
                "orphaned_org": val_result.orphaned_org,
                "orphaned_db": val_result.orphaned_db,
                "summary": val_result.summary,
            },
        }
        print(json.dumps(response, indent=2))

    except typer.Exit:
        raise
    except Exception as e:
        error_envelope = build_error_envelope(
            command="validate",
            code="INTERNAL_ERROR",
            message=str(e),
        )
        print(json.dumps(error_envelope, indent=2))
        logger.error("validate_failed", error=str(e))
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
