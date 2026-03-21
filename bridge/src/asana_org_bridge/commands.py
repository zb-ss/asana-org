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


@app.command()
def doctor() -> None:
    """Run diagnostics to verify bridge setup."""
    console.print("\n[bold]Asana Org Bridge - Diagnostics[/bold]\n")

    all_ok = True

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
        help="Filter by project GID (reserved for future use)",
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
            if json_input == "-":
                # Read from stdin
                input_data = sys.stdin.read()
                if input_data.strip():
                    try:
                        mutations_json = json.loads(input_data)
                    except json.JSONDecodeError as e:
                        error_envelope = build_error_envelope(
                            command="sync-apply",
                            code="INVALID_REQUEST",
                            message=f"Invalid JSON input: {e}",
                        )
                        print(json.dumps(error_envelope, indent=2))
                        raise typer.Exit(code=1) from None
            else:
                # Read from file
                input_path = Path(json_input)
                if not input_path.exists():
                    error_envelope = build_error_envelope(
                        command="sync-apply",
                        code="NOT_FOUND",
                        message=f"Input file not found: {json_input}",
                    )
                    print(json.dumps(error_envelope, indent=2))
                    raise typer.Exit(code=1)
                try:
                    with open(input_path) as f:
                        mutations_json = json.load(f)
                except json.JSONDecodeError as e:
                    # Return JSON error envelope for malformed JSON file
                    error_envelope = build_error_envelope(
                        command="sync-apply",
                        code="INVALID_REQUEST",
                        message=f"Invalid JSON in file {json_input}: {e}",
                    )
                    print(json.dumps(error_envelope, indent=2))
                    raise typer.Exit(code=1) from None
                except Exception as e:
                    error_envelope = build_error_envelope(
                        command="sync-apply",
                        code="INTERNAL_ERROR",
                        message=f"Failed to read input file: {e}",
                    )
                    print(json.dumps(error_envelope, indent=2))
                    raise typer.Exit(code=1) from None

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


if __name__ == "__main__":
    app()
