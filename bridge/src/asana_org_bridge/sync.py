"""Sync engine module for Asana Org Bridge."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from asana_org_bridge.asana_client import AsanaClient, create_asana_client
from asana_org_bridge.auth import AuthManager
from asana_org_bridge.config import get_settings
from asana_org_bridge.db import Database
from asana_org_bridge.logging_config import get_logger
from asana_org_bridge.models import (
    PendingMutation,
    RequestIdempotency,
    SyncRun,
    TaskSnapshot,
)

logger = get_logger(__name__)


@dataclass
class PullResult:
    """Result of a pull operation."""

    tasks_pulled: int = 0
    tasks_updated: int = 0
    errors: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(
        default_factory=list
    )  # Full task array for Elisp
    sections: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict
    )  # project_gid -> ordered section list


@dataclass
class PreviewResult:
    """Result of a preview operation."""

    mutations: list[PendingMutation] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # JSON contract fields
    preview_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplyResult:
    """Result of an apply operation."""

    applied: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    # JSON contract fields
    results_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class PruneResult:
    """Result of a cache prune operation."""

    snapshots_deleted: int = 0
    sync_runs_deleted: int = 0
    mutations_deleted: int = 0
    dry_run: bool = False


@dataclass
class DetectChangesResult:
    """Result of a detect-changes operation."""

    pending_changes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


class MockDataGenerator:
    """Generates deterministic mock data for testing."""

    # Sample data for deterministic mock mode
    MOCK_PROJECTS = [
        {"gid": "proj_001", "name": "Inbox"},
        {"gid": "proj_002", "name": "Personal"},
        {"gid": "proj_003", "name": "Work"},
    ]

    MOCK_SECTIONS = [
        {"gid": "sect_001", "name": "To Do", "project_gid": "proj_001"},
        {"gid": "sect_002", "name": "In Progress", "project_gid": "proj_001"},
        {"gid": "sect_003", "name": "Done", "project_gid": "proj_001"},
    ]

    MOCK_TASKS = [
        {
            "gid": "task_001",
            "name": "Review project proposal",
            "completed": False,
            "due_on": "2026-02-28",
            "start_on": "2026-02-25",
            "notes": "Need to review the Q1 project proposal document.",
            "project_gid": "proj_001",
            "section_gid": "sect_001",
            "memberships": [
                {
                    "project": {"gid": "proj_001", "name": "Inbox"},
                    "section": {"gid": "sect_001", "name": "To Do"},
                }
            ],
        },
        {
            "gid": "task_002",
            "name": "Email team about meeting",
            "completed": False,
            "due_on": "2026-02-27",
            "notes": "Send reminder about Thursday sync meeting.",
            "project_gid": "proj_002",
            "section_gid": "sect_001",
            "memberships": [
                {"project": {"gid": "proj_002", "name": "Personal"}, "section": None}
            ],
        },
        {
            "gid": "task_003",
            "name": "Complete quarterly report",
            "completed": True,
            "due_on": "2026-02-20",
            "start_on": "2026-02-15",
            "notes": "Q4 report submitted.",
            "project_gid": "proj_003",
            "section_gid": "sect_003",
            "memberships": [
                {
                    "project": {"gid": "proj_003", "name": "Work"},
                    "section": {"gid": "sect_003", "name": "Done"},
                }
            ],
        },
        {
            "gid": "task_004",
            "name": "Plan vacation",
            "completed": False,
            "due_on": "2026-03-15",
            "notes": "Research destinations and book flights.",
            "project_gid": "proj_002",
            "section_gid": "sect_001",
            "memberships": [
                {"project": {"gid": "proj_002", "name": "Personal"}, "section": None}
            ],
        },
        {
            "gid": "task_005",
            "name": "Update documentation",
            "completed": False,
            "due_at": "2026-02-26T17:00:00Z",
            "notes": "Update API documentation for new endpoints.",
            "project_gid": "proj_003",
            "section_gid": "sect_002",
            "memberships": [
                {
                    "project": {"gid": "proj_003", "name": "Work"},
                    "section": {"gid": "sect_002", "name": "In Progress"},
                }
            ],
        },
    ]

    @classmethod
    def generate_sections_by_project(cls) -> dict[str, list[dict[str, Any]]]:
        """Generate deterministic mock sections grouped by project."""
        by_project: dict[str, list[dict[str, Any]]] = {}
        for section in cls.MOCK_SECTIONS:
            pgid = section["project_gid"]
            if pgid not in by_project:
                by_project[pgid] = []
            by_project[pgid].append({"gid": section["gid"], "name": section["name"]})
        return by_project

    @classmethod
    def generate_tasks(cls) -> list[dict[str, Any]]:
        """Generate deterministic mock tasks."""

        tasks = []
        now = datetime.now(UTC)

        for task_data in cls.MOCK_TASKS:
            task = {
                "gid": task_data["gid"],
                "name": task_data["name"],
                "completed": task_data["completed"],
                "permalink_url": f"https://app.asana.com/0/0/{task_data['gid']}",
                "modified_at": now.isoformat(),
            }

            if "due_on" in task_data:
                task["due_on"] = task_data["due_on"]
            if "due_at" in task_data:
                task["due_at"] = task_data["due_at"]
            if "start_on" in task_data:
                task["start_on"] = task_data["start_on"]
            if "notes" in task_data:
                task["notes"] = task_data["notes"]

            # Build memberships
            memberships: list[dict[str, Any]] = []
            raw_memberships = task_data.get("memberships", [])
            if isinstance(raw_memberships, list):
                for membership in raw_memberships:
                    mem: dict[str, Any] = {
                        "project": membership["project"].copy(),
                    }
                    if membership.get("section"):
                        mem["section"] = membership["section"].copy()
                    memberships.append(mem)

            task["memberships"] = memberships
            tasks.append(task)

        return tasks

    @staticmethod
    def generate_stories(task_gid: str) -> list[dict[str, Any]]:
        """Generate deterministic mock comment stories for a task.

        Args:
            task_gid: The task GID to generate stories for

        Returns:
            List of 2-3 mock comment story dicts
        """
        # Use task_gid hash to produce deterministic but varied data
        seed = int(hashlib.md5(task_gid.encode()).hexdigest()[:8], 16)
        story_count = 2 + (seed % 2)  # 2 or 3 stories

        authors = [
            {"gid": "user_001", "name": "Alice Johnson"},
            {"gid": "user_002", "name": "Bob Smith"},
            {"gid": "user_003", "name": "Carol Williams"},
        ]

        comment_templates = [
            "I've reviewed this and it looks good to proceed.",
            "Could we schedule a follow-up meeting to discuss the details?",
            "Updated the timeline based on our latest discussion.",
            "Added the relevant documentation links.",
            "Let me know if you need any additional context on this.",
        ]

        stories: list[dict[str, Any]] = []
        for i in range(story_count):
            author = authors[(seed + i) % len(authors)]
            text = comment_templates[(seed + i) % len(comment_templates)]
            # Deterministic timestamps: 2026-02-20 + offset based on seed
            day_offset = (seed + i) % 10
            hour_offset = (seed + i * 3) % 24
            created_at = f"2026-02-{20 + day_offset:02d}T{hour_offset:02d}:00:00.000Z"

            stories.append({
                "gid": f"story_{task_gid}_{i + 1}",
                "created_by": author,
                "text": text,
                "created_at": created_at,
            })

        return stories


class SyncEngine:
    """Main sync engine for pulling and applying changes."""

    MAX_MUTATIONS_PER_REQUEST = 200
    MAX_TASK_GID_LENGTH = 64
    MAX_IDEMPOTENCY_KEY_LENGTH = 128
    MAX_COMMENT_TEXT_LENGTH = 5000
    MAX_PAYLOAD_FIELDS = 32
    MAX_PAYLOAD_BYTES = 32768

    def __init__(
        self,
        db: Database,
        auth_manager: AuthManager,
        use_mock: bool = False,
    ) -> None:
        """Initialize sync engine.

        Args:
            db: Database instance
            auth_manager: Authentication manager
            use_mock: Use mock data instead of API calls
        """
        self.db = db
        self.auth_manager = auth_manager
        self.use_mock = use_mock
        self._asana_client: AsanaClient | None = None

    @property
    def asana_client(self) -> AsanaClient | None:
        """Get or create Asana API client.

        Returns:
            AsanaClient instance or None if in mock mode
        """
        if self.use_mock:
            return None

        if self._asana_client is None:
            pat = self.auth_manager.get_pat()
            if pat:
                self._asana_client = create_asana_client(pat)
            else:
                logger.warning("no_pat_falling_back_to_mock")
                self.use_mock = True

        return self._asana_client

    @staticmethod
    def _asana_task_to_dict(asana_task: Any) -> dict[str, Any]:
        """Convert AsanaTask object to dict format for processing."""
        return {
            "gid": asana_task.gid,
            "name": asana_task.name,
            "completed": asana_task.completed,
            "permalink_url": asana_task.permalink_url,
            "modified_at": asana_task.modified_at.isoformat(),
            "due_on": asana_task.due_on,
            "due_at": asana_task.due_at,
            "start_on": asana_task.start_on,
            "notes": asana_task.notes,
            "memberships": asana_task.memberships,
        }

    def pull(
        self,
        force: bool = False,
        limit: int | None = None,
        incomplete_only: bool = False,
        modified_since: str | None = None,
        include_comments: bool = False,
        project_gid: str | None = None,
    ) -> PullResult:
        """Pull tasks from Asana and update local cache.

        Args:
            force: Force pull even if recently synced
            limit: Limit number of tasks to pull
            incomplete_only: Only pull incomplete tasks
            modified_since: Only pull tasks modified after this ISO date
            include_comments: Fetch and include task comments/stories
            project_gid: Filter tasks to only those belonging to this project

        Returns:
            PullResult with statistics
        """
        result = PullResult()

        # Create sync run record
        run = SyncRun(
            run_type="pull",
            status="started",
        )

        with self.db.session() as session:
            session.add(run)
            session.flush()

            try:
                if self.use_mock:
                    logger.info("using_mock_data")
                    tasks = MockDataGenerator.generate_tasks()
                    result.sections = MockDataGenerator.generate_sections_by_project()
                else:
                    # Use real Asana API via user_task_list for proper
                    # My Tasks section grouping
                    client = self.asana_client
                    if client:
                        logger.info("fetching_tasks_from_asana")
                        workspace_gid = get_settings().sync.workspace_gid
                        completed_since = "now" if incomplete_only else None

                        tasks = []
                        # Get user_task_list and its sections
                        if workspace_gid:
                            try:
                                utl = client.get_user_task_list(workspace_gid)
                                utl_gid = utl.get("gid")
                            except Exception:
                                utl_gid = None
                        else:
                            utl_gid = None

                        if utl_gid:
                            # Fetch My Tasks sections
                            my_sections = client.get_sections(utl_gid)
                            result.sections["my-tasks"] = my_sections

                            # Fetch tasks per section to preserve section assignment
                            seen_gids: set[str] = set()
                            for section in my_sections:
                                section_gid = section["gid"]
                                section_name = section["name"]
                                section_tasks = client.get_tasks_for_section(
                                    section_gid=section_gid,
                                    limit=limit or 100,
                                    completed_since=completed_since,
                                )
                                for asana_task in section_tasks:
                                    if asana_task.gid in seen_gids:
                                        continue
                                    seen_gids.add(asana_task.gid)
                                    task_dict = self._asana_task_to_dict(asana_task)
                                    # Inject the My Tasks section assignment
                                    task_dict["my_tasks_section_gid"] = section_gid
                                    task_dict["my_tasks_section_name"] = section_name
                                    tasks.append(task_dict)
                            logger.info(
                                "fetched_my_tasks_by_section",
                                total_tasks=len(tasks),
                                sections=len(my_sections),
                            )
                        else:
                            # Fallback: no workspace GID, use assignee endpoint
                            logger.warning("no_workspace_gid_using_assignee_endpoint")
                            asana_tasks = client.get_my_tasks(
                                workspace_gid=workspace_gid,
                                limit=limit or 100,
                                completed_since=completed_since,
                                modified_since=modified_since,
                            )
                            tasks = [
                                self._asana_task_to_dict(t) for t in asana_tasks
                            ]
                    else:
                        # Fallback to mock if client creation failed
                        logger.warning("api_client_unavailable_using_mock")
                        tasks = MockDataGenerator.generate_tasks()
                        result.sections = (
                            MockDataGenerator.generate_sections_by_project()
                        )

                # Filter by project membership if requested
                if project_gid:
                    pre_filter_count = len(tasks)
                    tasks = [
                        t
                        for t in tasks
                        if any(
                            m.get("project", {}).get("gid") == project_gid
                            for m in t.get("memberships", [])
                        )
                    ]
                    logger.info(
                        "project_filter_applied",
                        project_gid=project_gid,
                        before=pre_filter_count,
                        after=len(tasks),
                    )

                # Fetch comments/stories if requested
                if include_comments:
                    self._attach_stories_to_tasks(tasks)

                # Process tasks
                for task_data in tasks[:limit] if limit else tasks:
                    self._upsert_task_snapshot(session, task_data)
                    result.tasks_pulled += 1

                # Store full tasks array for Elisp consumption
                result.tasks = tasks[:limit] if limit else tasks

                # Update sync run
                run.status = "completed"
                run.tasks_pulled = result.tasks_pulled
                run.completed_at = datetime.now(UTC)

                result.tasks_updated = result.tasks_pulled
                logger.info("pull_completed", tasks_pulled=result.tasks_pulled)

            except Exception as e:
                run.status = "failed"
                run.errors = str(e)
                run.completed_at = datetime.now(UTC)
                result.errors.append(str(e))
                logger.error("pull_failed", error=str(e))
                raise

        # Auto-prune on every 10th successful pull
        if run.status == "completed":
            try:
                with self.db.session() as session:
                    pull_count = (
                        session.query(func.count(SyncRun.id))
                        .filter(
                            SyncRun.run_type == "pull",
                            SyncRun.status == "completed",
                        )
                        .scalar()
                    ) or 0

                if pull_count > 0 and pull_count % 10 == 0:
                    logger.info(
                        "auto_prune_triggered",
                        pull_count=pull_count,
                    )
                    self.prune_cache(dry_run=False)
            except Exception:
                logger.warning("auto_prune_failed", exc_info=True)

        return result

    def preview(self, as_json: bool = False) -> PreviewResult:
        """Preview pending mutations.

        Args:
            as_json: If True, generate JSON contract output

        Returns:
            PreviewResult with pending mutations and conflicts
        """
        result = PreviewResult()

        with self.db.session() as session:
            # Get pending mutations
            mutations = (
                session.query(PendingMutation)
                .filter(PendingMutation.status == "pending")
                .all()
            )

            result.mutations = mutations

            # Group mutations by type
            status_changes = []
            date_changes = []
            comments = []
            moves = []

            for mutation in mutations:
                operation = mutation.operation
                if operation in ("update_status", "complete_task", "uncomplete_task"):
                    status_changes.append(mutation)
                elif operation in ("update_dates", "update_due_on", "update_start_on"):
                    date_changes.append(mutation)
                elif operation in ("append_comment", "add_comment"):
                    comments.append(mutation)
                elif operation in ("update_section", "move_task", "update_project"):
                    moves.append(mutation)
                else:
                    # Default to moves for unknown operations
                    moves.append(mutation)

                # Check for conflicts (simplified - compare with latest snapshot)
                latest_snapshot = (
                    session.query(TaskSnapshot)
                    .filter(TaskSnapshot.gid == mutation.task_gid)
                    .order_by(TaskSnapshot.snapshot_at.desc())
                    .first()
                )

                # Simple conflict detection: if task was modified after mutation created
                if (
                    latest_snapshot
                    and latest_snapshot.modified_at > mutation.created_at
                ):
                    result.conflicts.append(
                        f"Task {mutation.task_gid} modified since mutation created"
                    )

            # Build JSON contract output
            if as_json:
                pending_changes = []
                for mutation in mutations:
                    change = self._mutation_to_preview_change(mutation, session)
                    pending_changes.append(change)

                result.preview_json = {
                    "version": "1",
                    "command": "sync-preview",
                    "status": "success",
                    "data": {
                        "pending_changes": pending_changes,
                    },
                    "summary": {
                        "total": len(mutations),
                        "status_changes": len(status_changes),
                        "date_changes": len(date_changes),
                        "comments": len(comments),
                        "moves": len(moves),
                    },
                }

                if result.conflicts:
                    result.preview_json["data"]["conflicts"] = result.conflicts
                    result.preview_json["data"]["has_blocking"] = any(
                        True for c in result.conflicts
                    )

                if result.warnings:
                    result.preview_json["warnings"] = result.warnings

            logger.info("preview_completed", pending=len(mutations))

        return result

    def _mutation_to_preview_change(
        self,
        mutation: PendingMutation,
        session: Session,
    ) -> dict[str, Any]:
        """Convert a pending mutation to preview change format.

        Args:
            mutation: The pending mutation
            session: Database session

        Returns:
            Dictionary in preview change format
        """
        operation = mutation.operation
        payload = mutation.payload

        # Get current state from latest snapshot
        latest_snapshot = (
            session.query(TaskSnapshot)
            .filter(TaskSnapshot.gid == mutation.task_gid)
            .order_by(TaskSnapshot.snapshot_at.desc())
            .first()
        )

        # Generate change ID from idempotency key (string), taking first 8 chars
        key_str = mutation.idempotency_key
        change_id = f"pc_{key_str[:8] if len(key_str) >= 8 else key_str}"

        # Determine mutation type
        if operation in ("update_status", "complete_task", "uncomplete_task"):
            change_type = "status_change"
            description = f"Update status for task {mutation.task_gid}"
            proposed_state = {
                "task_gid": mutation.task_gid,
                "completed": payload.get("completed", False),
            }
        elif operation in ("update_dates", "update_due_on", "update_start_on"):
            change_type = "date_change"
            description = f"Update dates for task {mutation.task_gid}"
            proposed_state = {
                "task_gid": mutation.task_gid,
            }
            if "due_on" in payload:
                proposed_state["due_on"] = payload["due_on"]
            if "start_on" in payload:
                proposed_state["start_on"] = payload["start_on"]
        elif operation in ("append_comment", "add_comment"):
            change_type = "comment_add"
            description = f"Add comment to task {mutation.task_gid}"
            proposed_state = {
                "task_gid": mutation.task_gid,
                "text": payload.get("text", ""),
            }
        elif operation in ("update_section", "move_task", "update_project"):
            change_type = "task_move"
            from_list = payload.get("from_list", payload.get("from_section", "Unknown"))
            to_list = payload.get("to_list", payload.get("to_section", "Unknown"))
            description = f'Move "{mutation.task_gid}" from {from_list} to {to_list}'
            proposed_state = {
                "task_gid": mutation.task_gid,
                "from_list": from_list,
                "to_list": to_list,
            }
        else:
            change_type = "unknown"
            description = f"Unknown operation: {operation}"
            proposed_state = payload

        change = {
            "id": change_id,
            "type": change_type,
            "description": description,
            "proposed_state": proposed_state,
            "idempotency_key": str(mutation.idempotency_key),
        }

        # Add current state if we have a snapshot (for conflict detection)
        if latest_snapshot:
            current_state: dict[str, Any] = {
                "task_gid": latest_snapshot.gid,
                "task_name": latest_snapshot.name,
                "modified_at": latest_snapshot.modified_at.isoformat(),
            }

            if operation in ("update_section", "move_task", "update_project"):
                current_state["current_list"] = latest_snapshot.section_name or "None"
            if operation in ("update_status", "complete_task", "uncomplete_task"):
                current_state["completed"] = latest_snapshot.completed
            if operation in ("update_dates", "update_due_on", "update_start_on"):
                current_state["due_on"] = latest_snapshot.due_on
                current_state["start_on"] = latest_snapshot.start_on

            change["current_state"] = current_state

            # Check for conflict
            if latest_snapshot.modified_at > mutation.created_at:
                change["conflict"] = {
                    "detected": True,
                    "reason": "Task was modified after mutation was created",
                    "baseline_modified_at": mutation.created_at.isoformat(),
                    "remote_modified_at": latest_snapshot.modified_at.isoformat(),
                    "blocking": True,
                }

        return change

    def apply(
        self, dry_run: bool = False, mutations_json: dict[str, Any] | None = None
    ) -> ApplyResult:
        """Apply pending mutations to Asana.

        Args:
            dry_run: If True, don't actually apply changes
            mutations_json: Optional JSON input with mutations array (for CLI contract)

        Returns:
            ApplyResult with statistics
        """
        # If mutations_json is provided, use it; otherwise get from database
        if mutations_json:
            return self.apply_from_json(mutations_json, dry_run)

        result = ApplyResult()

        run = SyncRun(
            run_type="apply",
            status="started",
        )

        with self.db.session() as session:
            session.add(run)
            session.flush()

            try:
                # Get pending mutations
                mutations = (
                    session.query(PendingMutation)
                    .filter(PendingMutation.status == "pending")
                    .all()
                )

                run.mutations_generated = len(mutations)

                # Safety cap: limit mutations processed per invocation
                settings = get_settings()
                write_cap = settings.sync.max_writes
                if write_cap > 0 and len(mutations) > write_cap:
                    logger.warning(
                        "write_cap_applied",
                        total_pending=len(mutations),
                        processing_limit=write_cap,
                    )
                    mutations = mutations[:write_cap]

                if dry_run:
                    logger.info("dry_run_mode", mutations=len(mutations))
                    result.applied = len(mutations)
                    run.status = "completed"
                    run.completed_at = datetime.now(UTC)
                    return result

                # Apply each mutation
                for mutation in mutations:
                    try:
                        if self.use_mock or not self.asana_client:
                            logger.info(
                                "mock_apply",
                                mutation_id=mutation.id,
                                operation=mutation.operation,
                            )
                        else:
                            api_result = self._apply_mutation_via_api(mutation, session)
                            if not api_result.get("success"):
                                raise RuntimeError(
                                    api_result.get("error", "Unknown API error")
                                )

                        # Mark as completed
                        mutation.status = "completed"
                        mutation.applied_at = datetime.now(UTC)
                        result.applied += 1

                    except Exception as e:
                        mutation.status = "failed"
                        mutation.error_message = str(e)
                        self._increment_attempts(mutation)
                        result.failed += 1
                        result.errors.append(f"Mutation {mutation.id}: {e}")
                        logger.error(
                            "mutation_failed", mutation_id=mutation.id, error=str(e)
                        )

                run.status = "completed"
                run.mutations_applied = result.applied
                run.completed_at = datetime.now(UTC)

                logger.info(
                    "apply_completed", applied=result.applied, failed=result.failed
                )

            except Exception as e:
                run.status = "failed"
                run.errors = str(e)
                run.completed_at = datetime.now(UTC)
                result.errors.append(str(e))
                logger.error("apply_failed", error=str(e))
                raise

        return result

    @staticmethod
    def _get_latest_snapshot(
        session: Session, task_gid: str
    ) -> TaskSnapshot | None:
        """Return the most recent TaskSnapshot for a given task GID."""
        return (
            session.query(TaskSnapshot)
            .filter(TaskSnapshot.gid == task_gid)
            .order_by(TaskSnapshot.snapshot_at.desc())
            .first()
        )

    @staticmethod
    def _ensure_aware(dt: datetime) -> datetime:
        """Ensure a datetime is timezone-aware (assume UTC if naive).

        SQLite may return naive datetimes despite DateTime(timezone=True).
        """
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt

    def _check_remote_conflict(
        self,
        client: AsanaClient,
        task_gid: str,
        mutation_type: str,
        payload: dict[str, Any],
        session: Session,
    ) -> dict[str, Any] | None:
        """Check for field-level conflicts between remote state and baseline.

        Fetches the current remote task from Asana and compares against the
        latest local TaskSnapshot (baseline). If the specific field being
        mutated was also changed remotely, returns a conflict dict. If only
        unrelated fields changed, returns a warning dict. Otherwise returns
        None (no conflict).

        Args:
            client: Asana API client
            task_gid: Task GID to check
            mutation_type: The operation type being applied
            payload: Mutation payload
            session: Database session for snapshot lookup

        Returns:
            Conflict/warning dict, or None if clean
        """
        try:
            remote_task = client.get_task(task_gid)
        except Exception as e:
            logger.warning(
                "conflict_check_remote_fetch_failed",
                task_gid=task_gid,
                error=str(e),
            )
            return {
                "type": "warning",
                "warning": f"Could not fetch remote task for conflict check: {e}",
            }

        baseline = self._get_latest_snapshot(session, task_gid)
        if baseline is None:
            logger.info("conflict_check_no_baseline", task_gid=task_gid)
            return {
                "type": "warning",
                "warning": "No baseline snapshot found for task; skipping conflict check.",
            }

        remote_modified_str = remote_task.get("modified_at", "")
        if not remote_modified_str:
            return None

        remote_modified_at = self._ensure_aware(
            datetime.fromisoformat(remote_modified_str.replace("Z", "+00:00"))
        )
        baseline_modified = self._ensure_aware(baseline.modified_at)

        if remote_modified_at <= baseline_modified:
            return None

        # Remote is newer -- check whether the *specific* mutated field changed
        conflict = self._detect_field_conflict(
            mutation_type, payload, remote_task, baseline
        )

        if conflict is not None:
            logger.warning(
                "conflict_detected_field_level",
                task_gid=task_gid,
                field=conflict["field"],
            )
            return {
                "type": "conflict",
                "status": "conflict",
                "mutation_type": mutation_type,
                "task_gid": task_gid,
                "conflict": {
                    **conflict,
                    "remote_modified_at": remote_modified_at.isoformat(),
                    "baseline_modified_at": baseline_modified.isoformat(),
                },
                "message": "Remote task was modified after your last pull. Pull again and re-apply.",
            }

        logger.info(
            "conflict_check_unrelated_change",
            task_gid=task_gid,
            remote_modified_at=remote_modified_at.isoformat(),
            baseline_modified_at=baseline_modified.isoformat(),
        )
        return {
            "type": "warning",
            "warning": "Remote task was modified since baseline, but the mutated field was not changed remotely.",
        }

    @staticmethod
    def _detect_field_conflict(
        mutation_type: str,
        payload: dict[str, Any],
        remote_task: dict[str, Any],
        baseline: TaskSnapshot,
    ) -> dict[str, Any] | None:
        """Return conflict details if the mutated field also changed remotely.

        Returns:
            Dict with field/baseline_value/remote_value/proposed_value,
            or None if no field-level conflict.
        """
        if mutation_type in ("update_status", "complete_task", "uncomplete_task"):
            remote_completed = remote_task.get("completed", False)
            if remote_completed != baseline.completed:
                return {
                    "field": "completed",
                    "baseline_value": baseline.completed,
                    "remote_value": remote_completed,
                    "proposed_value": payload.get("completed"),
                }

        elif mutation_type in ("update_dates", "update_due_on", "update_start_on"):
            if "due_on" in payload and remote_task.get("due_on") != baseline.due_on:
                return {
                    "field": "due_on",
                    "baseline_value": baseline.due_on,
                    "remote_value": remote_task.get("due_on"),
                    "proposed_value": payload.get("due_on"),
                }
            if "start_on" in payload and remote_task.get("start_on") != baseline.start_on:
                return {
                    "field": "start_on",
                    "baseline_value": baseline.start_on,
                    "remote_value": remote_task.get("start_on"),
                    "proposed_value": payload.get("start_on"),
                }

        return None

    def _apply_mutation_via_api(
        self,
        mutation: PendingMutation,
        session: Session,
    ) -> dict[str, Any]:
        """Apply a single mutation via Asana API.

        Performs remote conflict detection before executing the mutation.
        If the field being mutated was also changed on the remote, the
        mutation is blocked and a conflict result is returned.

        Args:
            mutation: The mutation to apply
            session: Database session

        Returns:
            Result dictionary with status and details
        """
        client = self.asana_client
        if not client:
            return {
                "success": False,
                "error": "API client not available",
                "error_code": "INTERNAL_ERROR",
            }

        operation = mutation.operation
        payload = mutation.payload
        task_gid = mutation.task_gid

        # Conflict detection: check remote state before applying
        # Skip for comment/move operations (comments are additive, moves
        # don't have a field-level conflict concept in the same way)
        apply_warning: str | None = None
        if operation in (
            "update_status",
            "complete_task",
            "uncomplete_task",
            "update_dates",
            "update_due_on",
            "update_start_on",
        ):
            conflict_result = self._check_remote_conflict(
                client, task_gid, operation, payload, session
            )
            if conflict_result is not None:
                if conflict_result.get("type") == "conflict":
                    return {
                        "success": False,
                        "error": conflict_result.get("message", "Conflict detected"),
                        "error_code": "CONFLICT",
                        "conflict": conflict_result.get("conflict"),
                        "status": "conflict",
                    }
                # Warning case: capture and continue
                apply_warning = conflict_result.get("warning")
                if apply_warning:
                    logger.info(
                        "apply_with_warning",
                        task_gid=task_gid,
                        warning=apply_warning,
                    )

        try:
            if operation in ("update_status", "complete_task", "uncomplete_task"):
                # Handle status/completion changes
                completed = payload.get("completed", False)
                result = client.update_task(task_gid, completed=completed)
                if result.success:
                    success_result: dict[str, Any] = {
                        "success": True,
                        "action": "update_status",
                        "task_gid": task_gid,
                        "completed": completed,
                    }
                    if apply_warning:
                        success_result["warning"] = apply_warning
                    return success_result
                return {
                    "success": False,
                    "error": result.error or "Unknown error",
                    "error_code": self._map_error_code(
                        result.error, result.status_code
                    ),
                }

            elif operation in ("update_dates", "update_due_on", "update_start_on"):
                # Handle date updates
                due_on = payload.get("due_on")
                start_on = payload.get("start_on")
                result = client.update_task(task_gid, due_on=due_on, start_on=start_on)
                if result.success:
                    success_result = {
                        "success": True,
                        "action": "update_dates",
                        "task_gid": task_gid,
                        "due_on": due_on,
                        "start_on": start_on,
                    }
                    if apply_warning:
                        success_result["warning"] = apply_warning
                    return success_result
                return {
                    "success": False,
                    "error": result.error or "Unknown error",
                    "error_code": self._map_error_code(
                        result.error, result.status_code
                    ),
                }

            elif operation in ("append_comment", "add_comment"):
                # Handle comment append
                text = payload.get("text", "")
                result = client.add_comment(task_gid, text)
                if result.success:
                    return {
                        "success": True,
                        "action": "comment_add",
                        "task_gid": task_gid,
                        "comment_gid": result.data.get("story_gid")
                        if result.data
                        else None,
                    }
                return {
                    "success": False,
                    "error": result.error or "Unknown error",
                    "error_code": self._map_error_code(
                        result.error, result.status_code
                    ),
                }

            elif operation in ("update_section", "move_task", "update_project"):
                # Handle task move
                to_section_gid = payload.get("to_section_gid")
                to_list = payload.get("to_list", "")
                if to_section_gid:
                    result = client.move_task_to_section(task_gid, to_section_gid)
                    if result.success:
                        return {
                            "success": True,
                            "action": "task_move",
                            "task_gid": task_gid,
                            "to_section_gid": to_section_gid,
                        }
                    return {
                        "success": False,
                        "error": result.error or "Unknown error",
                        "error_code": self._map_error_code(
                            result.error, result.status_code
                        ),
                    }

                # If destination already looks like a section gid, apply directly and
                # preserve API-derived error mapping without falling back to name resolution.
                if self._looks_like_section_gid(to_list):
                    result = client.move_task_to_section(task_gid, to_list)
                    if result.success:
                        return {
                            "success": True,
                            "action": "task_move",
                            "task_gid": task_gid,
                            "to_section_gid": to_list,
                        }
                    return {
                        "success": False,
                        "error": result.error or "Unknown error",
                        "error_code": self._map_error_code(
                            result.error, result.status_code
                        ),
                    }

                # If no section_gid, we need to resolve from name
                section_gid = self._resolve_section_gid(session, to_list, task_gid)
                if section_gid:
                    result = client.move_task_to_section(task_gid, section_gid)
                    if result.success:
                        return {
                            "success": True,
                            "action": "task_move",
                            "task_gid": task_gid,
                            "new_list": to_list,
                        }
                    return {
                        "success": False,
                        "error": result.error or "Unknown error",
                        "error_code": self._map_error_code(
                            result.error, result.status_code
                        ),
                    }
                return {
                    "success": False,
                    "error": f"Could not resolve section: {to_list}",
                    "error_code": "NOT_FOUND",
                }

            else:
                return {
                    "success": False,
                    "error": f"Unknown operation: {operation}",
                    "error_code": "INTERNAL_ERROR",
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_code": self._map_error_code(str(e)),
            }

    def _resolve_section_gid(
        self,
        session: Session,
        section_name: str,
        task_gid: str,
    ) -> str | None:
        """Resolve a section name to GID using current task memberships.

        Args:
            session: Database session
            section_name: Name of the section
            task_gid: Task GID to find its current project

        Returns:
            Section GID if found, None otherwise
        """
        # Get the task's current snapshot to find its project
        snapshot = (
            session.query(TaskSnapshot)
            .filter(TaskSnapshot.gid == task_gid)
            .order_by(TaskSnapshot.snapshot_at.desc())
            .first()
        )

        if not snapshot or not snapshot.project_gid:
            return None

        # Try to find a section with matching name in the project
        if self.asana_client:
            try:
                sections = self.asana_client.get_sections(snapshot.project_gid)
                for section in sections:
                    if section.get("name") == section_name:
                        return section.get("gid")
            except Exception:
                pass

        return None

    @staticmethod
    def _looks_like_section_gid(section_ref: str) -> bool:
        """Return True when the destination appears to be a section gid."""
        if not section_ref:
            return False
        return section_ref.isdigit() or section_ref.startswith("sect_")

    def _validate_section_in_project(
        self,
        target_section_gid: str,
        task_gid: str,
        from_list: str,
    ) -> dict[str, Any] | None:
        """Validate that a section GID belongs to the task's project.

        Args:
            target_section_gid: The target section GID to validate
            task_gid: Task GID being moved
            from_list: Source list/section hint (may contain project info)

        Returns:
            Error envelope dict if validation fails, None if valid
        """
        if self.use_mock or not self.asana_client:
            return self._validate_section_mock(target_section_gid, task_gid)

        return self._validate_section_api(
            target_section_gid, task_gid, from_list
        )

    def _validate_section_mock(
        self,
        target_section_gid: str,
        task_gid: str,
    ) -> dict[str, Any] | None:
        """Validate section against mock data.

        Args:
            target_section_gid: Section GID to validate
            task_gid: Task GID being moved

        Returns:
            Error envelope dict if validation fails, None if valid
        """
        project_gid = None
        for task_data in MockDataGenerator.MOCK_TASKS:
            if task_data["gid"] == task_gid:
                project_gid = task_data.get("project_gid")
                break

        if not project_gid:
            return None

        project_name = project_gid
        for proj in MockDataGenerator.MOCK_PROJECTS:
            if proj["gid"] == project_gid:
                project_name = proj["name"]
                break

        valid_sections = [
            s for s in MockDataGenerator.MOCK_SECTIONS
            if s["project_gid"] == project_gid
        ]

        valid_gids = {s["gid"] for s in valid_sections}
        if target_section_gid in valid_gids:
            return None

        section_list = self._format_section_list(valid_sections)
        return self._build_invalid_section_error(
            target_section_gid, project_name, section_list
        )

    def _validate_section_api(
        self,
        target_section_gid: str,
        task_gid: str,
        from_list: str,
    ) -> dict[str, Any] | None:
        """Validate section against live Asana API.

        Determines the task's project from the from_list argument or by
        fetching the task's current memberships, then checks that the
        target section belongs to that project.

        Args:
            target_section_gid: Section GID to validate
            task_gid: Task GID being moved
            from_list: Source list/section hint

        Returns:
            Error envelope dict if validation fails, None if valid
        """
        client = self.asana_client
        if not client:
            return None

        project_gid: str | None = None
        project_name: str | None = None

        if from_list and (from_list.isdigit() or from_list.startswith("proj_")):
            project_gid = from_list

        if not project_gid:
            try:
                task = client.get_task(task_gid)
                if task.memberships:
                    first_membership = task.memberships[0]
                    project_obj = first_membership.get("project", {})
                    project_gid = project_obj.get("gid")
                    project_name = project_obj.get("name")
            except Exception:
                logger.warning(
                    "section_validation_task_fetch_failed",
                    task_gid=task_gid,
                )
                return None

        if not project_gid:
            return None

        try:
            sections = client.get_sections(project_gid)
        except Exception:
            logger.warning(
                "section_validation_sections_fetch_failed",
                project_gid=project_gid,
            )
            return None

        valid_gids = {s.get("gid") for s in sections}
        if target_section_gid in valid_gids:
            return None

        if not project_name:
            project_name = project_gid

        section_list = self._format_section_list(sections)
        return self._build_invalid_section_error(
            target_section_gid, project_name, section_list
        )

    @staticmethod
    def _format_section_list(sections: list[dict[str, Any]]) -> str:
        """Format a list of sections as 'Name (gid), ...' for error messages."""
        return ", ".join(
            f"{s.get('name', '?')} ({s.get('gid', '?')})" for s in sections
        )

    @staticmethod
    def _build_invalid_section_error(
        target: str,
        project_name: str,
        section_list: str,
    ) -> dict[str, Any]:
        """Build an INVALID_SECTION error envelope.

        Args:
            target: The invalid section GID
            project_name: Name or GID of the project
            section_list: Formatted list of valid sections

        Returns:
            Error envelope dict
        """
        return {
            "version": "1",
            "command": "move-task",
            "status": "error",
            "error": {
                "code": "INVALID_SECTION",
                "message": (
                    f"Section '{target}' not found in project "
                    f"'{project_name}'. Valid sections: {section_list}"
                ),
            },
        }

    @staticmethod
    def _increment_attempts(mutation: PendingMutation) -> None:
        """Increment attempt counter safely when database rows contain null."""
        mutation.attempts = (mutation.attempts or 0) + 1

    # Allowed mutation types per contract
    ALLOWED_MUTATION_TYPES = frozenset(
        ["task_move", "comment_add", "status_change", "date_change"]
    )

    @staticmethod
    def _map_error_code(
        error_message: str | None, status_code: int | None = None
    ) -> str:
        """Map API and exception failures to contract error codes."""
        if status_code == 404:
            return "NOT_FOUND"
        if status_code == 429:
            return "RATE_LIMITED"
        if status_code in (401, 403):
            return "AUTH_ERROR"
        if status_code == 409:
            return "CONFLICT"

        message = (error_message or "").lower()
        if "not found" in message or "missing" in message:
            return "NOT_FOUND"
        if (
            "unauthorized" in message
            or "forbidden" in message
            or "authentication" in message
            or "auth" in message
        ):
            return "AUTH_ERROR"
        if "conflict" in message:
            return "CONFLICT"
        if "rate limit" in message or "too many requests" in message:
            return "RATE_LIMITED"

        return "INTERNAL_ERROR"

    def apply_from_json(
        self, mutations_json: dict[str, Any], dry_run: bool = False
    ) -> ApplyResult:
        """Apply mutations from JSON contract input.

        Args:
            mutations_json: JSON with mutations array and idempotency keys
            dry_run: If True, don't actually apply changes

        Returns:
            ApplyResult with per-mutation status
        """
        result = ApplyResult()

        # Validate top-level structure
        if not isinstance(mutations_json, dict):
            result.errors.append("Input must be a JSON object")
            return result

        # Validate required contract fields: version and command
        version = mutations_json.get("version")
        if not version:
            result.errors.append("Missing required field 'version' in request")
        elif version != "1":
            result.errors.append(f"Unsupported version '{version}': expected '1'")

        command = mutations_json.get("command")
        if not command:
            result.errors.append("Missing required field 'command' in request")
        elif command != "sync-apply":
            result.errors.append(f"Invalid command '{command}': expected 'sync-apply'")

        request_idempotency_key = mutations_json.get("idempotency_key")
        if request_idempotency_key is not None:
            if not isinstance(request_idempotency_key, str):
                result.errors.append("Field 'idempotency_key' must be a string")
            elif not request_idempotency_key.strip():
                result.errors.append("Field 'idempotency_key' cannot be empty")
            elif len(request_idempotency_key) > self.MAX_IDEMPOTENCY_KEY_LENGTH:
                result.errors.append(
                    f"Field 'idempotency_key' exceeds max length {self.MAX_IDEMPOTENCY_KEY_LENGTH}"
                )

        # Check for mutations array
        if "mutations" not in mutations_json:
            result.errors.append("Missing 'mutations' array in input")
            return result

        mutations_data = mutations_json.get("mutations", [])
        if not isinstance(mutations_data, list):
            result.errors.append("'mutations' must be an array")
            return result

        # Safety cap: policy limit on writes per invocation (configurable)
        settings = get_settings()
        write_cap = settings.sync.max_writes
        if write_cap > 0 and len(mutations_data) > write_cap:
            result.results_json = {
                "version": "1",
                "command": "sync-apply",
                "status": "error",
                "error": {
                    "code": "WRITE_LIMIT_EXCEEDED",
                    "message": (
                        f"Mutation count ({len(mutations_data)}) exceeds maximum "
                        f"writes per invocation ({write_cap}). Split into smaller "
                        f"batches or increase ASANA_ORG_MAX_WRITES."
                    ),
                },
            }
            result.errors.append(result.results_json["error"]["message"])
            logger.warning(
                "write_limit_exceeded",
                mutation_count=len(mutations_data),
                max_writes=write_cap,
            )
            return result

        if len(mutations_data) > self.MAX_MUTATIONS_PER_REQUEST:
            result.errors.append(
                f"'mutations' exceeds maximum of {self.MAX_MUTATIONS_PER_REQUEST} entries"
            )

        # Validate each mutation has required fields
        for i, mut in enumerate(mutations_data):
            if not isinstance(mut, dict):
                result.errors.append(f"Mutation {i}: must be an object")
                continue

            mutation_type = mut.get("type", "")
            if not mutation_type:
                result.errors.append(f"Mutation {i}: missing 'type' field")
            elif mutation_type not in self.ALLOWED_MUTATION_TYPES:
                result.errors.append(
                    f"Mutation {i}: unknown type '{mutation_type}'. "
                    f"Allowed: {', '.join(self.ALLOWED_MUTATION_TYPES)}"
                )

            mutation_idempotency_key = mut.get("idempotency_key")
            if mutation_idempotency_key is not None:
                if not isinstance(mutation_idempotency_key, str):
                    result.errors.append(
                        f"Mutation {i}: 'idempotency_key' must be a string"
                    )
                elif not mutation_idempotency_key.strip():
                    result.errors.append(
                        f"Mutation {i}: 'idempotency_key' cannot be empty"
                    )
                elif len(mutation_idempotency_key) > self.MAX_IDEMPOTENCY_KEY_LENGTH:
                    result.errors.append(
                        f"Mutation {i}: 'idempotency_key' exceeds max length {self.MAX_IDEMPOTENCY_KEY_LENGTH}"
                    )

            payload = mut.get("payload")
            if payload is None:
                result.errors.append(f"Mutation {i}: missing 'payload' field")
            elif not isinstance(payload, dict):
                result.errors.append(f"Mutation {i}: 'payload' must be an object")

            # Validate required payload fields based on type
            if isinstance(payload, dict):
                if len(payload) > self.MAX_PAYLOAD_FIELDS:
                    result.errors.append(
                        f"Mutation {i}: payload has too many fields (max {self.MAX_PAYLOAD_FIELDS})"
                    )

                try:
                    payload_size = len(json.dumps(payload).encode("utf-8"))
                except (TypeError, ValueError):
                    result.errors.append(
                        f"Mutation {i}: payload must be JSON-serializable"
                    )
                    payload_size = 0

                if payload_size > self.MAX_PAYLOAD_BYTES:
                    result.errors.append(
                        f"Mutation {i}: payload exceeds size limit ({self.MAX_PAYLOAD_BYTES} bytes)"
                    )

                task_gid = payload.get("task_gid")
                if not isinstance(task_gid, str) or not task_gid.strip():
                    result.errors.append(f"Mutation {i}: payload missing 'task_gid'")
                elif len(task_gid) > self.MAX_TASK_GID_LENGTH:
                    result.errors.append(
                        f"Mutation {i}: 'task_gid' exceeds max length {self.MAX_TASK_GID_LENGTH}"
                    )

                # Type-specific validation
                if (
                    mutation_type == "task_move"
                    and "to_list" not in payload
                    and "to_section_gid" not in payload
                ):
                    result.errors.append(
                        f"Mutation {i}: task_move requires 'to_list' or 'to_section_gid' in payload"
                    )
                if mutation_type == "task_move":
                    to_list = payload.get("to_list")
                    if to_list is not None and (
                        not isinstance(to_list, str) or not to_list.strip()
                    ):
                        result.errors.append(
                            f"Mutation {i}: 'to_list' must be a non-empty string"
                        )
                    to_section_gid = payload.get("to_section_gid")
                    if to_section_gid is not None and (
                        not isinstance(to_section_gid, str)
                        or not to_section_gid.strip()
                        or len(to_section_gid) > self.MAX_TASK_GID_LENGTH
                    ):
                        result.errors.append(
                            f"Mutation {i}: 'to_section_gid' must be a non-empty string with max length {self.MAX_TASK_GID_LENGTH}"
                        )
                elif mutation_type == "comment_add" and "text" not in payload:
                    result.errors.append(
                        f"Mutation {i}: comment_add requires 'text' in payload"
                    )
                elif mutation_type == "comment_add":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        result.errors.append(f"Mutation {i}: 'text' must be a string")
                    elif not text.strip():
                        result.errors.append(f"Mutation {i}: 'text' cannot be empty")
                    elif len(text) > self.MAX_COMMENT_TEXT_LENGTH:
                        result.errors.append(
                            f"Mutation {i}: 'text' exceeds max length {self.MAX_COMMENT_TEXT_LENGTH}"
                        )

        # Return early if validation errors
        if result.errors:
            return result

        # Create a hash of the request for integrity checking on retry
        request_hash = (
            hashlib.sha256(
                json.dumps(mutations_data, sort_keys=True).encode()
            ).hexdigest()
            if mutations_data
            else None
        )

        # Check for request-level idempotency (dedupe repeated requests)
        if request_idempotency_key:
            with self.db.session() as check_session:
                existing_request = (
                    check_session.query(RequestIdempotency)
                    .filter(
                        RequestIdempotency.idempotency_key == request_idempotency_key
                    )
                    .first()
                )

                if existing_request:
                    # Request already processed - return cached response deterministically
                    if (
                        existing_request.status == "completed"
                        and existing_request.response_json
                    ):
                        # Check for hash mismatch - protection against different payload with same key
                        if (
                            request_hash
                            and existing_request.request_hash
                            and existing_request.request_hash != request_hash
                        ):
                            logger.error(
                                "request_hash_mismatch",
                                idempotency_key=request_idempotency_key,
                                expected_hash=existing_request.request_hash,
                                actual_hash=request_hash,
                            )
                            result.errors.append(
                                f"Request hash mismatch: idempotency_key '{request_idempotency_key}' "
                                f"was used with a different request payload. "
                                f"Use a new idempotency_key for a different request."
                            )
                            return result

                        logger.info(
                            "request_idempotency_hit",
                            idempotency_key=request_idempotency_key,
                            status=existing_request.status,
                        )
                        # Return cached response
                        cached_response = json.loads(existing_request.response_json)
                        result.results_json = cached_response
                        # Parse summary from cached response
                        if (
                            "data" in cached_response
                            and "summary" in cached_response["data"]
                        ):
                            summary = cached_response["data"]["summary"]
                            result.applied = summary.get("applied", 0)
                            result.failed = summary.get("failed", 0)
                        return result
                    elif existing_request.status == "failed":
                        # Previous attempt failed - allow retry with same key
                        logger.info(
                            "request_idempotency_retry",
                            idempotency_key=request_idempotency_key,
                            previous_status="failed",
                        )
                    # else: in-progress or other state - continue processing

        run = SyncRun(
            run_type="apply",
            status="started",
        )

        with self.db.session() as session:
            session.add(run)
            session.flush()

            try:
                # Process each mutation
                results_list = []
                for mut_data in mutations_data:
                    idempotency_key = mut_data.get("idempotency_key", str(uuid4()))
                    mutation_type = mut_data.get("type", "")
                    payload = mut_data.get("payload", {})

                    # Check for existing mutation with same idempotency key
                    existing = (
                        session.query(PendingMutation)
                        .filter(PendingMutation.idempotency_key == idempotency_key)
                        .first()
                    )

                    if existing and existing.status == "completed":
                        # Already applied - return success
                        results_list.append(
                            {
                                "idempotency_key": str(idempotency_key),
                                "status": "applied",
                                "details": {
                                    "action": mutation_type,
                                    "message": "Already applied (idempotent)",
                                },
                            }
                        )
                        continue

                    # Determine operation type
                    operation = self._map_mutation_type_to_operation(
                        mutation_type, payload
                    )

                    if dry_run:
                        # Just record what would be applied
                        results_list.append(
                            {
                                "idempotency_key": str(idempotency_key),
                                "status": "applied",
                                "details": {
                                    "action": mutation_type,
                                    "operation": operation,
                                    "dry_run": True,
                                },
                            }
                        )
                        result.applied += 1
                        continue

                    # Get task_gid before creating mutation
                    task_gid = payload.get("task_gid", "")

                    # Determine baseline timestamp for conflict detection
                    # Priority: 1) baseline_timestamp from request (preview baseline)
                    #           2) existing mutation's created_at (retry)
                    #           3) current time (new mutation, no baseline)
                    baseline_timestamp: datetime | None = None

                    # Check for explicit baseline_timestamp in mutation data (from preview)
                    baseline_str = mut_data.get("baseline_timestamp")
                    if baseline_str:
                        try:
                            # Parse ISO format timestamp
                            baseline_timestamp = datetime.fromisoformat(
                                baseline_str.replace("Z", "+00:00")
                            )
                            logger.debug(
                                "using_preview_baseline",
                                idempotency_key=str(idempotency_key),
                                baseline_timestamp=baseline_str,
                            )
                        except (ValueError, TypeError) as e:
                            logger.warning(
                                "invalid_baseline_timestamp",
                                idempotency_key=str(idempotency_key),
                                baseline_timestamp=baseline_str,
                                error=str(e),
                            )

                    # Fall back to existing mutation's created_at
                    if baseline_timestamp is None and existing and existing.created_at:
                        baseline_timestamp = existing.created_at

                    # Check for conflict with current state using baseline
                    latest_snapshot = (
                        session.query(TaskSnapshot)
                        .filter(TaskSnapshot.gid == task_gid)
                        .order_by(TaskSnapshot.snapshot_at.desc())
                        .first()
                    )

                    conflict_detected = False
                    conflict_reason = ""
                    if (
                        baseline_timestamp
                        and latest_snapshot
                        and latest_snapshot.modified_at > baseline_timestamp
                    ):
                        conflict_detected = True
                        # Determine specific conflict reason based on mutation type
                        if mutation_type == "task_move":
                            conflict_reason = f"Task was moved to '{latest_snapshot.section_name or 'Unknown'}' after baseline (modified at {latest_snapshot.modified_at.isoformat()})"
                        elif mutation_type == "status_change":
                            conflict_reason = f"Task completion status changed to {latest_snapshot.completed} after baseline (modified at {latest_snapshot.modified_at.isoformat()})"
                        elif mutation_type in ("date_change", "update_dates"):
                            conflict_reason = f"Task dates were updated after baseline (modified at {latest_snapshot.modified_at.isoformat()})"
                        else:
                            conflict_reason = f"Task was modified after baseline (modified at {latest_snapshot.modified_at.isoformat()})"

                    mutation: PendingMutation | None = None
                    try:
                        # Create or update mutation record
                        if existing:
                            mutation = existing
                            mutation.operation = operation
                            mutation.payload = payload
                            self._increment_attempts(mutation)
                        else:
                            mutation = PendingMutation(
                                task_gid=task_gid,
                                operation=operation,
                                payload=payload,
                                idempotency_key=idempotency_key,
                                status="applying",
                            )
                            session.add(mutation)

                        # Check conflict before applying - use local reference for type safety
                        snapshot_for_conflict = latest_snapshot
                        if conflict_detected and snapshot_for_conflict is not None:
                            # Record conflict but don't apply
                            mutation.status = "failed"
                            mutation.error_message = conflict_reason
                            self._increment_attempts(mutation)

                            results_list.append(
                                {
                                    "idempotency_key": str(idempotency_key),
                                    "status": "conflict",
                                    "details": {
                                        "action": mutation_type,
                                        "task_gid": task_gid,
                                        "reason": conflict_reason,
                                        "current_state": {
                                            "section": snapshot_for_conflict.section_name,
                                            "completed": snapshot_for_conflict.completed,
                                            "modified_at": snapshot_for_conflict.modified_at.isoformat(),
                                        },
                                    },
                                }
                            )
                            result.failed += 1
                            logger.warning(
                                "mutation_conflict_detected",
                                idempotency_key=str(idempotency_key),
                                reason=conflict_reason,
                            )
                            continue

                        # Apply mutation via API (or mock if no client)
                        if self.use_mock or not self.asana_client:
                            logger.info(
                                "mock_apply",
                                mutation_id=mutation.id,
                                operation=operation,
                            )

                            # Simulate different result types based on mutation type
                            if mutation_type == "task_move":
                                details = {
                                    "action": "task_move",
                                    "task_gid": task_gid,
                                    "new_list": payload.get("to_list", ""),
                                }
                            elif mutation_type == "comment_add":
                                details = {
                                    "action": "comment_add",
                                    "comment_gid": f"comment_{mutation.id}",
                                    "task_gid": task_gid,
                                }
                            else:
                                details = {
                                    "action": mutation_type,
                                    "task_gid": task_gid,
                                }

                            results_list.append(
                                {
                                    "idempotency_key": str(idempotency_key),
                                    "status": "applied",
                                    "details": details,
                                }
                            )
                        else:
                            # Real API apply (includes remote conflict detection)
                            api_result = self._apply_mutation_via_api(mutation, session)

                            if api_result.get("success"):
                                entry: dict[str, Any] = {
                                    "idempotency_key": str(idempotency_key),
                                    "status": "applied",
                                    "details": api_result,
                                }
                                # Propagate warning from conflict check
                                if api_result.get("warning"):
                                    entry["warning"] = api_result["warning"]
                                results_list.append(entry)
                            elif api_result.get("status") == "conflict":
                                # Remote conflict detected -- block mutation
                                mutation.status = "failed"
                                mutation.error_message = api_result.get(
                                    "error", "Conflict detected"
                                )
                                self._increment_attempts(mutation)

                                conflict_entry: dict[str, Any] = {
                                    "idempotency_key": str(idempotency_key),
                                    "status": "conflict",
                                    "mutation_type": mutation_type,
                                    "task_gid": task_gid,
                                    "message": api_result.get("error", "Conflict detected"),
                                }
                                if api_result.get("conflict"):
                                    conflict_entry["conflict"] = api_result["conflict"]
                                results_list.append(conflict_entry)
                                result.failed += 1
                                result.errors.append(
                                    f"Mutation {idempotency_key}: {api_result.get('error')}"
                                )
                                logger.warning(
                                    "mutation_remote_conflict",
                                    idempotency_key=str(idempotency_key),
                                    error=api_result.get("error"),
                                )
                                continue
                            else:
                                # API call failed
                                mutation.status = "failed"
                                mutation.error_message = api_result.get(
                                    "error", "Unknown error"
                                )
                                self._increment_attempts(mutation)

                                results_list.append(
                                    {
                                        "idempotency_key": str(idempotency_key),
                                        "status": "error",
                                        "details": {
                                            "action": mutation_type,
                                            "task_gid": task_gid,
                                            "code": api_result.get(
                                                "error_code", "INTERNAL_ERROR"
                                            ),
                                            "error": api_result.get("error"),
                                        },
                                    }
                                )
                                result.failed += 1
                                result.errors.append(
                                    f"Mutation {idempotency_key}: {api_result.get('error')}"
                                )
                                logger.error(
                                    "mutation_api_failed",
                                    idempotency_key=str(idempotency_key),
                                    error=api_result.get("error"),
                                )
                                continue

                        # Mark as completed
                        mutation.status = "completed"
                        mutation.applied_at = datetime.now(UTC)
                        result.applied += 1

                    except Exception as e:
                        if mutation:
                            mutation.status = "failed"
                            mutation.error_message = str(e)
                            self._increment_attempts(mutation)

                        results_list.append(
                            {
                                "idempotency_key": str(idempotency_key),
                                "status": "error",
                                "details": {
                                    "code": self._map_error_code(str(e)),
                                    "error": str(e),
                                },
                            }
                        )
                        result.failed += 1
                        result.errors.append(f"Mutation {idempotency_key}: {e}")
                        logger.error(
                            "mutation_failed",
                            idempotency_key=str(idempotency_key),
                            error=str(e),
                        )

                # Count conflicts in results
                conflicts_count = sum(
                    1 for r in results_list if r.get("status") == "conflict"
                )

                # Build JSON response (wrapped in data for elisp compatibility)
                result.results_json = {
                    "version": "1",
                    "command": "sync-apply",
                    "status": "success" if result.failed == 0 else "partial",
                    "data": {
                        "results": results_list,
                        "summary": {
                            "total": len(mutations_data),
                            "applied": result.applied,
                            "failed": result.failed,
                            "conflicts": conflicts_count,
                        },
                    },
                }

                # Store request idempotency record for deduplication
                if request_idempotency_key:
                    idempotency_record = RequestIdempotency(
                        idempotency_key=request_idempotency_key,
                        request_hash=request_hash,
                        status="completed",
                        response_json=json.dumps(result.results_json),
                        completed_at=datetime.now(UTC),
                    )
                    session.add(idempotency_record)
                    logger.info(
                        "request_idempotency_stored",
                        idempotency_key=request_idempotency_key,
                        request_hash=request_hash,
                    )

                run.status = "completed"
                run.mutations_applied = result.applied
                run.mutations_generated = len(mutations_data)
                run.conflicts_detected = conflicts_count
                run.completed_at = datetime.now(UTC)

                logger.info(
                    "apply_completed",
                    applied=result.applied,
                    failed=result.failed,
                    conflicts=conflicts_count,
                )

            except Exception as e:
                run.status = "failed"
                run.errors = str(e)
                run.completed_at = datetime.now(UTC)
                result.errors.append(str(e))
                logger.error("apply_failed", error=str(e))
                raise

        return result

    def _map_mutation_type_to_operation(
        self, mutation_type: str, payload: dict[str, Any]
    ) -> str:
        """Map CLI contract mutation types to internal operation names.

        Args:
            mutation_type: Type from CLI contract
            payload: Mutation payload

        Returns:
            Internal operation name
        """
        type_map = {
            "task_move": "update_section",
            "comment_add": "append_comment",
            "status_change": "update_status",
            "date_change": "update_dates",
        }
        return type_map.get(mutation_type, mutation_type)

    def execute_move_task(
        self,
        task_gid: str,
        from_list: str,
        to_list: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute a move-task command.

        Args:
            task_gid: Task GID to move
            from_list: Source list/section
            to_list: Destination list/section
            idempotency_key: Optional idempotency key

        Returns:
            Result dictionary in CLI contract format
        """
        key = idempotency_key or str(uuid4())

        payload = {
            "task_gid": task_gid,
            "from_list": from_list,
            "to_list": to_list,
        }

        with self.db.session() as session:
            # Check if already applied
            existing = (
                session.query(PendingMutation)
                .filter(PendingMutation.idempotency_key == key)
                .first()
            )

            if existing and existing.status == "completed":
                return {
                    "version": "1",
                    "command": "move-task",
                    "status": "success",
                    "data": {
                        "result": {
                            "idempotency_key": key,
                            "status": "applied",
                            "task_gid": task_gid,
                            "new_list": to_list,
                            "message": "Already applied (idempotent)",
                        },
                    },
                }

            mutation = PendingMutation(
                task_gid=task_gid,
                operation="update_section",
                payload=payload,
                idempotency_key=key,
                status="applying",
            )
            session.add(mutation)
            session.flush()

            # Validate target section belongs to the task's project
            if self._looks_like_section_gid(to_list):
                validation_error = self._validate_section_in_project(
                    to_list, task_gid, from_list
                )
                if validation_error:
                    mutation.status = "failed"
                    mutation.error_message = validation_error["error"]["message"]
                    self._increment_attempts(mutation)
                    return validation_error

            if self.use_mock or not self.asana_client:
                logger.info(
                    "move_task_executed_mock", task_gid=task_gid, to_list=to_list
                )
                mutation.status = "completed"
                mutation.applied_at = datetime.now(UTC)
                return {
                    "version": "1",
                    "command": "move-task",
                    "status": "success",
                    "data": {
                        "result": {
                            "idempotency_key": key,
                            "status": "applied",
                            "task_gid": task_gid,
                            "new_list": to_list,
                        },
                    },
                }

            api_result = self._apply_mutation_via_api(mutation, session)
            if not api_result.get("success"):
                error_message = api_result.get("error", "Failed to move task")
                mutation.status = "failed"
                mutation.error_message = str(error_message)
                self._increment_attempts(mutation)
                return {
                    "version": "1",
                    "command": "move-task",
                    "status": "error",
                    "error": {
                        "code": api_result.get("error_code", "INTERNAL_ERROR"),
                        "message": str(error_message),
                    },
                }

            mutation.status = "completed"
            mutation.applied_at = datetime.now(UTC)

            logger.info("move_task_executed", task_gid=task_gid, to_list=to_list)

            return {
                "version": "1",
                "command": "move-task",
                "status": "success",
                "data": {
                    "result": {
                        "idempotency_key": key,
                        "status": "applied",
                        "task_gid": task_gid,
                        "new_list": to_list,
                    },
                },
            }

    def execute_comment_append(
        self, task_gid: str, text: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Execute a comment-append command.

        Args:
            task_gid: Task GID to comment on
            text: Comment text
            idempotency_key: Optional idempotency key

        Returns:
            Result dictionary in CLI contract format
        """

        key = idempotency_key or str(uuid4())

        payload = {
            "task_gid": task_gid,
            "text": text,
        }

        with self.db.session() as session:
            # Check if already applied
            existing = (
                session.query(PendingMutation)
                .filter(PendingMutation.idempotency_key == key)
                .first()
            )

            if existing and existing.status == "completed":
                return {
                    "version": "1",
                    "command": "comment-append",
                    "status": "success",
                    "data": {
                        "result": {
                            "idempotency_key": key,
                            "status": "applied",
                            "task_gid": task_gid,
                            "comment_gid": f"comment_{existing.id}",
                            "message": "Already applied (idempotent)",
                        },
                    },
                }

            mutation = PendingMutation(
                task_gid=task_gid,
                operation="append_comment",
                payload=payload,
                idempotency_key=key,
                status="applying",
            )
            session.add(mutation)
            session.flush()

            if self.use_mock or not self.asana_client:
                logger.info("comment_append_executed_mock", task_gid=task_gid)
                mutation.status = "completed"
                mutation.applied_at = datetime.now(UTC)
                return {
                    "version": "1",
                    "command": "comment-append",
                    "status": "success",
                    "data": {
                        "result": {
                            "idempotency_key": key,
                            "status": "applied",
                            "comment_gid": f"comment_{mutation.id}",
                            "task_gid": task_gid,
                        },
                    },
                }

            api_result = self._apply_mutation_via_api(mutation, session)
            if not api_result.get("success"):
                error_message = api_result.get("error", "Failed to append comment")
                mutation.status = "failed"
                mutation.error_message = str(error_message)
                self._increment_attempts(mutation)
                return {
                    "version": "1",
                    "command": "comment-append",
                    "status": "error",
                    "error": {
                        "code": api_result.get("error_code", "INTERNAL_ERROR"),
                        "message": str(error_message),
                    },
                }

            mutation.status = "completed"
            mutation.applied_at = datetime.now(UTC)
            comment_gid = api_result.get("comment_gid") or api_result.get(
                "details", {}
            ).get("comment_gid")

            logger.info("comment_append_executed", task_gid=task_gid)

            return {
                "version": "1",
                "command": "comment-append",
                "status": "success",
                "data": {
                    "result": {
                        "idempotency_key": key,
                        "status": "applied",
                        "comment_gid": comment_gid,
                        "task_gid": task_gid,
                    },
                },
            }

    def _attach_stories_to_tasks(
        self,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Fetch and attach comment stories to each task in-place.

        Uses the Asana API in live mode or MockDataGenerator in mock mode.

        Args:
            tasks: List of task dicts to enrich with stories
        """
        client = self.asana_client
        use_mock = self.use_mock or client is None

        for task_data in tasks:
            task_gid = task_data.get("gid", "")
            if not task_gid:
                continue

            try:
                if use_mock:
                    stories = MockDataGenerator.generate_stories(task_gid)
                else:
                    assert client is not None  # guaranteed by use_mock check
                    stories = client.get_stories(task_gid)

                task_data["stories"] = stories
                logger.debug(
                    "fetched_stories",
                    task_gid=task_gid,
                    story_count=len(stories),
                )
            except Exception as e:
                logger.warning(
                    "stories_fetch_failed",
                    task_gid=task_gid,
                    error=str(e),
                )
                task_data["stories"] = []

    def get_status(self) -> dict[str, Any]:
        """Query sync health information from the database.

        Returns:
            Dictionary with sync status metrics including last pull/apply
            timestamps, snapshot counts, mutation counts, and DB metadata.
        """
        db_path = str(self.db.db_path)
        db_size_bytes: int | None = None
        try:
            db_size_bytes = os.path.getsize(self.db.db_path)
        except OSError:
            db_size_bytes = None

        schema_version = self.db.get_schema_version()

        # If DB is not initialized, return zeros/nulls
        if schema_version is None:
            return {
                "last_pull_at": None,
                "last_apply_at": None,
                "snapshot_count": 0,
                "unique_tasks": 0,
                "pending_mutations": 0,
                "failed_mutations": 0,
                "total_sync_runs": 0,
                "schema_version": None,
                "db_size_bytes": db_size_bytes,
                "db_path": db_path,
            }

        with self.db.session() as session:
            # Last completed pull
            last_pull_run = (
                session.query(SyncRun)
                .filter(SyncRun.run_type == "pull", SyncRun.status == "completed")
                .order_by(SyncRun.completed_at.desc())
                .first()
            )
            last_pull_at = (
                last_pull_run.completed_at.isoformat() if last_pull_run else None
            )

            # Last completed apply
            last_apply_run = (
                session.query(SyncRun)
                .filter(SyncRun.run_type == "apply", SyncRun.status == "completed")
                .order_by(SyncRun.completed_at.desc())
                .first()
            )
            last_apply_at = (
                last_apply_run.completed_at.isoformat() if last_apply_run else None
            )

            # Snapshot counts
            snapshot_count = session.query(TaskSnapshot).count()

            unique_tasks_result = session.query(
                func.count(func.distinct(TaskSnapshot.gid))
            ).scalar()
            unique_tasks = unique_tasks_result or 0

            # Mutation counts
            pending_mutations = (
                session.query(PendingMutation)
                .filter(PendingMutation.status == "pending")
                .count()
            )
            failed_mutations = (
                session.query(PendingMutation)
                .filter(PendingMutation.status == "failed")
                .count()
            )

            # Total sync runs
            total_sync_runs = session.query(SyncRun).count()

        return {
            "last_pull_at": last_pull_at,
            "last_apply_at": last_apply_at,
            "snapshot_count": snapshot_count,
            "unique_tasks": unique_tasks,
            "pending_mutations": pending_mutations,
            "failed_mutations": failed_mutations,
            "total_sync_runs": total_sync_runs,
            "schema_version": schema_version,
            "db_size_bytes": db_size_bytes,
            "db_path": db_path,
        }

    def _upsert_task_snapshot(
        self,
        session: Session,
        task_data: dict[str, Any],
    ) -> None:
        """Insert or update a task snapshot.

        Args:
            session: Database session
            task_data: Task data from API
        """
        import json

        gid = task_data.get("gid", "")
        modified_at_str = task_data.get("modified_at", "")
        modified_at = datetime.fromisoformat(modified_at_str.replace("Z", "+00:00"))

        # Find existing snapshot
        session.query(TaskSnapshot).filter(TaskSnapshot.gid == gid).order_by(
            TaskSnapshot.snapshot_at.desc()
        ).first()

        # Parse memberships
        memberships_json = json.dumps(task_data.get("memberships", []))
        project_gid = None
        project_name = None
        section_gid = None
        section_name = None

        if task_data.get("memberships"):
            first_mem = task_data["memberships"][0]
            if first_mem.get("project"):
                project_gid = first_mem["project"].get("gid")
                project_name = first_mem["project"].get("name")
            if first_mem.get("section"):
                section_gid = first_mem["section"].get("gid")
                section_name = first_mem["section"].get("name")

        # Serialize stories if present
        stories_data = task_data.get("stories")
        stories_json_str = json.dumps(stories_data) if stories_data else None

        snapshot = TaskSnapshot(
            gid=gid,
            permalink_url=task_data.get("permalink_url", ""),
            name=task_data.get("name", ""),
            completed=task_data.get("completed", False),
            start_on=task_data.get("start_on"),
            due_on=task_data.get("due_on"),
            due_at=task_data.get("due_at"),
            notes=task_data.get("notes"),
            project_gid=project_gid,
            project_name=project_name,
            section_gid=section_gid,
            section_name=section_name,
            memberships_json=memberships_json,
            stories_json=stories_json_str,
            modified_at=modified_at,
        )

        session.add(snapshot)

    def prune_cache(self, dry_run: bool = False) -> PruneResult:
        """Prune old cache entries based on retention policy.

        Deletes old snapshots, sync runs, and completed mutations
        according to configured retention periods. Always keeps the
        most recent snapshot per task GID and never deletes snapshots
        referenced by pending/failed mutations.

        Args:
            dry_run: If True, count what would be deleted without deleting

        Returns:
            PruneResult with deletion counts
        """
        settings = get_settings()
        result = PruneResult(dry_run=dry_run)
        now = datetime.now(UTC)

        snapshot_cutoff = now - timedelta(days=settings.sync.snapshot_retention_days)
        journal_cutoff = now - timedelta(days=settings.sync.journal_retention_days)
        audit_cutoff = now - timedelta(days=settings.sync.audit_retention_days)

        with self.db.session() as session:
            # --- Snapshots ---
            # Find the most recent snapshot ID per task GID (always kept)
            latest_per_task = (
                session.query(func.max(TaskSnapshot.id))
                .group_by(TaskSnapshot.gid)
                .all()
            )
            protected_snapshot_ids = {row[0] for row in latest_per_task}

            # Find task GIDs referenced by pending/failed mutations
            protected_task_gids_rows = (
                session.query(PendingMutation.task_gid)
                .filter(PendingMutation.status.in_(["pending", "failed"]))
                .distinct()
                .all()
            )
            protected_task_gids = {row[0] for row in protected_task_gids_rows}

            # Find snapshot IDs for protected task GIDs (all snapshots for those tasks)
            if protected_task_gids:
                protected_by_mutation_rows = (
                    session.query(TaskSnapshot.id)
                    .filter(TaskSnapshot.gid.in_(protected_task_gids))
                    .all()
                )
                protected_snapshot_ids.update(
                    row[0] for row in protected_by_mutation_rows
                )

            # Query old snapshots eligible for deletion
            old_snapshots_query = session.query(TaskSnapshot).filter(
                TaskSnapshot.snapshot_at < snapshot_cutoff,
            )
            if protected_snapshot_ids:
                old_snapshots_query = old_snapshots_query.filter(
                    TaskSnapshot.id.notin_(protected_snapshot_ids),
                )

            if dry_run:
                result.snapshots_deleted = old_snapshots_query.count()
            else:
                result.snapshots_deleted = old_snapshots_query.delete(
                    synchronize_session="fetch"
                )

            # --- Sync Runs ---
            old_runs_query = session.query(SyncRun).filter(
                SyncRun.started_at < journal_cutoff,
            )

            if dry_run:
                result.sync_runs_deleted = old_runs_query.count()
            else:
                result.sync_runs_deleted = old_runs_query.delete(
                    synchronize_session="fetch"
                )

            # --- Completed Mutations ---
            # Only delete completed/succeeded mutations; never pending/failed
            old_mutations_query = session.query(PendingMutation).filter(
                PendingMutation.created_at < audit_cutoff,
                PendingMutation.status.notin_(["pending", "failed"]),
            )

            if dry_run:
                result.mutations_deleted = old_mutations_query.count()
            else:
                result.mutations_deleted = old_mutations_query.delete(
                    synchronize_session="fetch"
                )

        logger.info(
            "cache_prune_completed",
            dry_run=dry_run,
            snapshots_deleted=result.snapshots_deleted,
            sync_runs_deleted=result.sync_runs_deleted,
            mutations_deleted=result.mutations_deleted,
        )

        return result

    def detect_changes(
        self,
        task_states: list[dict[str, Any]],
    ) -> DetectChangesResult:
        """Detect changes between org file state and latest snapshots.

        Compares each task's current org state against the most recent
        TaskSnapshot in the database and generates mutation entries for
        any differences found.

        Args:
            task_states: List of dicts with keys: gid, completed, due_on,
                         start_on, local_hash

        Returns:
            DetectChangesResult with pending_changes and summary
        """
        result = DetectChangesResult()
        status_changes = 0
        date_changes = 0

        with self.db.session() as session:
            for task_state in task_states:
                gid = task_state.get("gid", "")
                if not gid:
                    result.warnings.append("Skipped task with empty gid")
                    continue

                # Look up the latest snapshot for this task
                snapshot: TaskSnapshot | None
                if self.use_mock:
                    snapshot = self._get_mock_snapshot(gid, session)
                else:
                    snapshot = (
                        session.query(TaskSnapshot)
                        .filter(TaskSnapshot.gid == gid)
                        .order_by(TaskSnapshot.snapshot_at.desc())
                        .first()
                    )

                if snapshot is None:
                    result.warnings.append(
                        f"Task {gid} not found in database, skipped"
                    )
                    continue

                org_completed = bool(task_state.get("completed", False))
                org_due_on = task_state.get("due_on") or None
                org_start_on = task_state.get("start_on") or None

                # Compare completed status
                if org_completed != snapshot.completed:
                    change = self._build_change_entry(
                        change_type="status_change",
                        task_gid=gid,
                        description=(
                            f"Mark task {gid} as "
                            f"{'completed' if org_completed else 'incomplete'}"
                        ),
                        current_state={
                            "task_gid": gid,
                            "task_name": snapshot.name,
                            "completed": snapshot.completed,
                            "modified_at": snapshot.modified_at.isoformat(),
                        },
                        proposed_state={
                            "task_gid": gid,
                            "completed": org_completed,
                        },
                        baseline_modified_at=snapshot.modified_at.isoformat(),
                    )
                    result.pending_changes.append(change)
                    status_changes += 1

                # Compare due_on
                snapshot_due_on = snapshot.due_on or None
                if org_due_on != snapshot_due_on:
                    change = self._build_change_entry(
                        change_type="date_change",
                        task_gid=gid,
                        description=(
                            f"Change due date for task {gid} "
                            f"from {snapshot_due_on} to {org_due_on}"
                        ),
                        current_state={
                            "task_gid": gid,
                            "task_name": snapshot.name,
                            "due_on": snapshot_due_on,
                            "modified_at": snapshot.modified_at.isoformat(),
                        },
                        proposed_state={
                            "task_gid": gid,
                            "due_on": org_due_on,
                        },
                        baseline_modified_at=snapshot.modified_at.isoformat(),
                    )
                    result.pending_changes.append(change)
                    date_changes += 1

                # Compare start_on
                snapshot_start_on = snapshot.start_on or None
                if org_start_on != snapshot_start_on:
                    change = self._build_change_entry(
                        change_type="date_change",
                        task_gid=gid,
                        description=(
                            f"Change start date for task {gid} "
                            f"from {snapshot_start_on} to {org_start_on}"
                        ),
                        current_state={
                            "task_gid": gid,
                            "task_name": snapshot.name,
                            "start_on": snapshot_start_on,
                            "modified_at": snapshot.modified_at.isoformat(),
                        },
                        proposed_state={
                            "task_gid": gid,
                            "start_on": org_start_on,
                        },
                        baseline_modified_at=snapshot.modified_at.isoformat(),
                    )
                    result.pending_changes.append(change)
                    date_changes += 1

        result.summary = {
            "total": len(result.pending_changes),
            "status_changes": status_changes,
            "date_changes": date_changes,
        }

        logger.info(
            "detect_changes_completed",
            total=result.summary["total"],
            status_changes=status_changes,
            date_changes=date_changes,
            warnings=len(result.warnings),
        )

        return result

    def _get_mock_snapshot(
        self, gid: str, session: Session
    ) -> TaskSnapshot | None:
        """Get a snapshot for mock mode, creating one from mock data if needed.

        Args:
            gid: Task GID to look up
            session: Database session

        Returns:
            TaskSnapshot or None if task not found in mock data
        """
        # First try the database (may have been populated by a prior pull)
        snapshot = (
            session.query(TaskSnapshot)
            .filter(TaskSnapshot.gid == gid)
            .order_by(TaskSnapshot.snapshot_at.desc())
            .first()
        )
        if snapshot:
            return snapshot

        # Fall back to generating from mock data
        for mock_task in MockDataGenerator.MOCK_TASKS:
            if mock_task["gid"] == gid:
                now = datetime.now(UTC)
                snapshot = TaskSnapshot(
                    gid=mock_task["gid"],
                    permalink_url=f"https://app.asana.com/0/0/{mock_task['gid']}",
                    name=mock_task["name"],
                    completed=mock_task.get("completed", False),
                    start_on=mock_task.get("start_on"),
                    due_on=mock_task.get("due_on"),
                    notes=mock_task.get("notes"),
                    modified_at=now,
                )
                session.add(snapshot)
                session.flush()
                return snapshot

        return None

    @staticmethod
    def _build_change_entry(
        change_type: str,
        task_gid: str,
        description: str,
        current_state: dict[str, Any],
        proposed_state: dict[str, Any],
        baseline_modified_at: str,
    ) -> dict[str, Any]:
        """Build a single change entry for detect-changes output.

        Args:
            change_type: Type of change (status_change, date_change)
            task_gid: Task GID
            description: Human-readable description
            current_state: Current state from snapshot
            proposed_state: Proposed state from org
            baseline_modified_at: Snapshot modified_at ISO string

        Returns:
            Change entry dictionary
        """
        # Generate a deterministic change ID from content
        content = f"{task_gid}:{change_type}:{json.dumps(proposed_state, sort_keys=True)}"
        change_hash = hashlib.sha256(content.encode()).hexdigest()[:8]

        return {
            "id": f"pc_{change_hash}",
            "type": change_type,
            "description": description,
            "current_state": current_state,
            "proposed_state": proposed_state,
            "baseline_modified_at": baseline_modified_at,
        }
