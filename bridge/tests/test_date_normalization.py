"""Tests for date/time normalization, DST edge cases, and SQLite round-trips.

Covers UTC parsing, timezone offset handling, naive-datetime recovery,
null date preservation, DST spring-forward/fall-back, and ISO format
consistency across the bridge pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pytest

from asana_org_bridge.asana_client import AsanaClient, AsanaTask
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import TaskSnapshot
from asana_org_bridge.sync import SyncEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "date_norm.db"


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    db = Database(db_path=temp_db_path, echo=False)
    MigrationManager(db).migrate()
    return db


@pytest.fixture
def auth_manager() -> AuthManagerProto:
    class MockAuthManager:
        def get_pat(self) -> str:
            return "mock_pat"

    return MockAuthManager()


@pytest.fixture
def engine(database: Database, auth_manager: AuthManagerProto) -> SyncEngine:
    return SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]


def _insert_snapshot(
    database: Database,
    gid: str,
    name: str = "Test Task",
    completed: bool = False,
    due_on: str | None = "2026-03-15",
    due_at: str | None = None,
    start_on: str | None = "2026-03-10",
    modified_at: datetime | None = None,
) -> None:
    """Insert a TaskSnapshot for testing."""
    with database.session() as session:
        snapshot = TaskSnapshot(
            gid=gid,
            permalink_url=f"https://app.asana.com/0/0/{gid}",
            name=name,
            completed=completed,
            due_on=due_on,
            due_at=due_at,
            start_on=start_on,
            notes="Some notes",
            modified_at=modified_at or datetime.now(UTC),
        )
        session.add(snapshot)


# ---------------------------------------------------------------------------
# (a) UTC normalization — "Z" suffix → timezone-aware UTC
# ---------------------------------------------------------------------------


class TestUtcNormalization:
    """Verify that modified_at with 'Z' suffix is correctly parsed to UTC."""

    def test_parse_task_z_suffix_becomes_utc_aware(self) -> None:
        """_parse_task converts '...Z' modified_at to tz-aware UTC datetime."""
        client = AsanaClient.__new__(AsanaClient)
        task_data: dict[str, Any] = {
            "gid": "12345",
            "name": "UTC test",
            "completed": False,
            "permalink_url": "https://app.asana.com/0/0/12345",
            "modified_at": "2026-03-15T10:30:00Z",
            "due_on": None,
            "due_at": None,
            "start_on": None,
            "notes": "",
            "memberships": [],
        }
        task = client._parse_task(task_data)

        assert task.modified_at.tzinfo is not None
        assert task.modified_at.utcoffset() == timedelta(0)
        assert task.modified_at.year == 2026
        assert task.modified_at.month == 3
        assert task.modified_at.day == 15
        assert task.modified_at.hour == 10
        assert task.modified_at.minute == 30

    def test_upsert_snapshot_z_suffix_parsed(self, database: Database, engine: SyncEngine) -> None:
        """_upsert_task_snapshot correctly parses Z-suffix modified_at."""
        task_data: dict[str, Any] = {
            "gid": "utc_test_001",
            "name": "UTC snapshot",
            "completed": False,
            "permalink_url": "https://app.asana.com/0/0/utc_test_001",
            "modified_at": "2026-06-10T08:00:00Z",
            "due_on": None,
            "due_at": None,
            "start_on": None,
            "notes": "",
            "memberships": [],
        }

        with database.session() as session:
            engine._upsert_task_snapshot(session, task_data)

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="utc_test_001").first()
            assert snap is not None
            aware_modified = SyncEngine._ensure_aware(snap.modified_at)
            assert aware_modified.utcoffset() == timedelta(0)
            assert aware_modified.hour == 8


# ---------------------------------------------------------------------------
# (b) Timezone offset parsing
# ---------------------------------------------------------------------------


class TestTimezoneOffsetParsing:
    """Verify that due_at with explicit offset is parsed correctly."""

    def test_parse_task_negative_offset(self) -> None:
        """_parse_task stores due_at string with negative offset as-is."""
        client = AsanaClient.__new__(AsanaClient)
        task_data: dict[str, Any] = {
            "gid": "tz_001",
            "name": "TZ test",
            "completed": False,
            "permalink_url": "https://app.asana.com/0/0/tz_001",
            "modified_at": "2026-03-08T19:00:00+00:00",
            "due_on": "2026-03-08",
            "due_at": "2026-03-08T14:00:00-05:00",
            "start_on": None,
            "notes": "",
            "memberships": [],
        }
        task = client._parse_task(task_data)

        # due_at is stored as a raw string in the AsanaTask dataclass
        assert task.due_at == "2026-03-08T14:00:00-05:00"

    def test_due_at_offset_stored_in_snapshot(self, database: Database) -> None:
        """due_at with offset survives snapshot storage and retrieval."""
        _insert_snapshot(
            database,
            gid="tz_snap_001",
            due_at="2026-03-08T14:00:00-05:00",
        )

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="tz_snap_001").first()
            assert snap is not None
            assert snap.due_at == "2026-03-08T14:00:00-05:00"


# ---------------------------------------------------------------------------
# (c) Date-only vs datetime
# ---------------------------------------------------------------------------


class TestDateOnlyVsDatetime:
    """Verify that due_on (date) and due_at (datetime) are stored differently."""

    def test_due_on_is_date_string(self) -> None:
        """due_on is a plain YYYY-MM-DD string without time component."""
        client = AsanaClient.__new__(AsanaClient)
        task_data: dict[str, Any] = {
            "gid": "date_001",
            "name": "Date test",
            "completed": False,
            "permalink_url": "https://app.asana.com/0/0/date_001",
            "modified_at": "2026-03-15T12:00:00Z",
            "due_on": "2026-03-15",
            "due_at": "2026-03-15T17:00:00.000Z",
            "start_on": None,
            "notes": "",
            "memberships": [],
        }
        task = client._parse_task(task_data)

        assert task.due_on == "2026-03-15"
        assert "T" not in (task.due_on or "")
        assert task.due_at is not None
        assert "T" in task.due_at

    def test_snapshot_stores_both_formats(self, database: Database) -> None:
        """Snapshot preserves date-only (due_on) and datetime (due_at)."""
        _insert_snapshot(
            database,
            gid="date_snap_001",
            due_on="2026-03-15",
            due_at="2026-03-15T17:00:00.000Z",
        )

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="date_snap_001").first()
            assert snap is not None
            assert snap.due_on == "2026-03-15"
            assert snap.due_at == "2026-03-15T17:00:00.000Z"


# ---------------------------------------------------------------------------
# (d) SQLite round-trip — timezone-aware datetime survives store/load
# ---------------------------------------------------------------------------


class TestSqliteRoundTrip:
    """SQLite strips tzinfo; _ensure_aware must recover it."""

    def test_modified_at_round_trip_via_ensure_aware(self, database: Database) -> None:
        """Store tz-aware modified_at, read back, apply _ensure_aware."""
        original = datetime(2026, 7, 4, 14, 30, 0, tzinfo=UTC)
        _insert_snapshot(database, gid="rt_001", modified_at=original)

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="rt_001").first()
            assert snap is not None

            # SQLite may return a naive datetime
            recovered = SyncEngine._ensure_aware(snap.modified_at)
            assert recovered.tzinfo is not None
            assert recovered.utcoffset() == timedelta(0)

            # The wall-clock values must match the original
            assert recovered.year == 2026
            assert recovered.month == 7
            assert recovered.day == 4
            assert recovered.hour == 14
            assert recovered.minute == 30

    def test_ensure_aware_already_aware_is_noop(self) -> None:
        """_ensure_aware returns an already-aware datetime unchanged."""
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = SyncEngine._ensure_aware(aware)
        assert result is aware  # identity, not just equality

    def test_ensure_aware_naive_becomes_utc(self) -> None:
        """_ensure_aware attaches UTC to a naive datetime."""
        naive = datetime(2026, 1, 1, 12, 0, 0)
        result = SyncEngine._ensure_aware(naive)
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result.hour == 12  # wall-clock preserved

    def test_created_at_snapshot_at_round_trip(self, database: Database) -> None:
        """created_at and snapshot_at survive the SQLite round-trip."""
        _insert_snapshot(database, gid="rt_002")

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="rt_002").first()
            assert snap is not None
            # These are populated by model defaults
            created = SyncEngine._ensure_aware(snap.created_at)
            snapped = SyncEngine._ensure_aware(snap.snapshot_at)
            assert created.tzinfo is not None
            assert snapped.tzinfo is not None


# ---------------------------------------------------------------------------
# (e) Null date handling
# ---------------------------------------------------------------------------


class TestNullDateHandling:
    """Verify None/null dates are preserved through the pipeline."""

    def test_null_dates_preserved_in_snapshot(self, database: Database) -> None:
        """None values for due_on, due_at, start_on survive storage."""
        _insert_snapshot(
            database,
            gid="null_001",
            due_on=None,
            due_at=None,
            start_on=None,
        )

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="null_001").first()
            assert snap is not None
            assert snap.due_on is None
            assert snap.due_at is None
            assert snap.start_on is None

    def test_null_dates_no_false_diff_in_detect_changes(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """detect_changes does not flag None==None as a change."""
        _insert_snapshot(
            database, gid="null_dc_001", due_on=None, start_on=None
        )

        result = engine.detect_changes(
            [{"gid": "null_dc_001", "completed": False, "due_on": None, "start_on": None}]
        )
        assert len(result.pending_changes) == 0

    def test_null_to_value_detected(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """Going from None to a real date is detected as a change."""
        _insert_snapshot(
            database, gid="null_dc_002", due_on=None, start_on=None
        )

        result = engine.detect_changes(
            [{"gid": "null_dc_002", "completed": False, "due_on": "2026-04-01", "start_on": None}]
        )
        assert len(result.pending_changes) == 1
        assert result.pending_changes[0]["type"] == "date_change"

    def test_value_to_null_detected(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """Going from a real date to None is detected as a change."""
        _insert_snapshot(
            database, gid="null_dc_003", due_on="2026-03-15", start_on="2026-03-10"
        )

        result = engine.detect_changes(
            [{"gid": "null_dc_003", "completed": False, "due_on": None, "start_on": None}]
        )
        assert len(result.pending_changes) == 2
        assert result.summary["date_changes"] == 2

    def test_parse_task_null_dates(self) -> None:
        """_parse_task handles null due_on / due_at / start_on from API."""
        client = AsanaClient.__new__(AsanaClient)
        task_data: dict[str, Any] = {
            "gid": "null_api",
            "name": "Null dates",
            "completed": False,
            "permalink_url": "https://app.asana.com/0/0/null_api",
            "modified_at": "2026-01-01T00:00:00Z",
            "memberships": [],
        }
        task = client._parse_task(task_data)
        assert task.due_on is None
        assert task.due_at is None
        assert task.start_on is None


# ---------------------------------------------------------------------------
# (f) DST spring-forward edge case
# ---------------------------------------------------------------------------


class TestDstSpringForward:
    """Test datetimes near DST spring-forward (gap time)."""

    def test_spring_forward_gap_time_does_not_crash(self) -> None:
        """Parsing a wall-clock time in the spring-forward gap must not crash.

        2026-03-08T02:30:00 America/New_York does not exist (clocks jump 02:00→03:00).
        The Asana API always returns UTC/offset-qualified strings, so the bridge
        never constructs this as a local time. But we verify robustness: a UTC
        timestamp whose wall-clock equivalent *would* be in the gap is handled
        fine, because the bridge only works in UTC.
        """
        # 02:30 EST = 07:30 UTC; the bridge stores UTC
        utc_time_str = "2026-03-08T07:30:00Z"
        parsed = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

        # Converting to America/New_York should yield 03:30 EDT (after the jump)
        eastern = ZoneInfo("America/New_York")
        local = parsed.astimezone(eastern)
        # After spring-forward, UTC 07:30 → EDT 03:30
        assert local.hour == 3
        assert local.minute == 30

    def test_spring_forward_snapshot_storage(self, database: Database) -> None:
        """Snapshot with modified_at near DST transition stores correctly."""
        # 2026-03-08T07:30:00 UTC (would be gap time in Eastern)
        near_dst = datetime(2026, 3, 8, 7, 30, 0, tzinfo=UTC)
        _insert_snapshot(database, gid="dst_spring_001", modified_at=near_dst)

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="dst_spring_001").first()
            assert snap is not None
            recovered = SyncEngine._ensure_aware(snap.modified_at)
            assert recovered.hour == 7
            assert recovered.minute == 30


# ---------------------------------------------------------------------------
# (g) DST fall-back edge case
# ---------------------------------------------------------------------------


class TestDstFallBack:
    """Test datetimes near DST fall-back (ambiguous time)."""

    def test_fall_back_ambiguous_time_does_not_crash(self) -> None:
        """01:30 America/New_York on 2026-11-01 is ambiguous (occurs in EDT and EST).

        Again, the bridge works in UTC, so this is not a problem in practice.
        We verify that a UTC timestamp whose Eastern equivalent is ambiguous
        can be converted both ways without error.
        """
        # 01:30 EDT = 05:30 UTC; 01:30 EST = 06:30 UTC
        utc_edt_str = "2026-11-01T05:30:00+00:00"
        utc_est_str = "2026-11-01T06:30:00+00:00"

        parsed_edt = datetime.fromisoformat(utc_edt_str)
        parsed_est = datetime.fromisoformat(utc_est_str)

        eastern = ZoneInfo("America/New_York")
        local_edt = parsed_edt.astimezone(eastern)
        local_est = parsed_est.astimezone(eastern)

        # Both convert to 01:30 Eastern, but at different UTC instants
        assert local_edt.hour == 1
        assert local_edt.minute == 30
        assert local_est.hour == 1
        assert local_est.minute == 30

        # They differ in fold or offset
        assert parsed_edt != parsed_est

    def test_fall_back_snapshot_storage(self, database: Database) -> None:
        """Snapshot near fall-back transition stores and retrieves correctly."""
        near_fallback = datetime(2026, 11, 1, 5, 30, 0, tzinfo=UTC)
        _insert_snapshot(database, gid="dst_fall_001", modified_at=near_fallback)

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="dst_fall_001").first()
            assert snap is not None
            recovered = SyncEngine._ensure_aware(snap.modified_at)
            assert recovered.hour == 5
            assert recovered.minute == 30
            assert recovered.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# (h) Date comparison in detect_changes
# ---------------------------------------------------------------------------


class TestDateComparisonInDetectChanges:
    """Verify date string comparisons behave correctly."""

    def test_same_date_string_no_change(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """Identical date strings produce no change."""
        _insert_snapshot(
            database, gid="cmp_001", due_on="2026-03-15", start_on="2026-03-10"
        )
        result = engine.detect_changes(
            [{"gid": "cmp_001", "completed": False, "due_on": "2026-03-15", "start_on": "2026-03-10"}]
        )
        assert len(result.pending_changes) == 0

    def test_different_date_string_detected(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """Different date strings are detected as changes."""
        _insert_snapshot(
            database, gid="cmp_002", due_on="2026-03-15", start_on="2026-03-10"
        )
        result = engine.detect_changes(
            [{"gid": "cmp_002", "completed": False, "due_on": "2026-03-16", "start_on": "2026-03-11"}]
        )
        assert len(result.pending_changes) == 2
        assert result.summary["date_changes"] == 2

    def test_empty_string_treated_as_null(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """Empty string dates from org are treated as None (no false diff)."""
        _insert_snapshot(
            database, gid="cmp_003", due_on=None, start_on=None
        )
        result = engine.detect_changes(
            [{"gid": "cmp_003", "completed": False, "due_on": "", "start_on": ""}]
        )
        assert len(result.pending_changes) == 0

    def test_modified_at_isoformat_used_in_baseline(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """baseline_modified_at in change entries uses ISO format."""
        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
        _insert_snapshot(
            database, gid="cmp_004", completed=False, modified_at=ts
        )
        result = engine.detect_changes(
            [{"gid": "cmp_004", "completed": True, "due_on": "2026-03-15", "start_on": "2026-03-10"}]
        )
        assert len(result.pending_changes) >= 1
        change = result.pending_changes[0]
        baseline = change["baseline_modified_at"]
        # Must parse as a valid ISO datetime
        parsed = datetime.fromisoformat(baseline)
        assert parsed.year == 2026


# ---------------------------------------------------------------------------
# (i) ISO format consistency in JSON responses
# ---------------------------------------------------------------------------


class TestIsoFormatConsistency:
    """Verify that all date outputs use consistent ISO format."""

    def test_asana_task_to_dict_iso_format(self) -> None:
        """_asana_task_to_dict serializes modified_at as ISO string."""
        task = AsanaTask(
            gid="iso_001",
            name="ISO test",
            completed=False,
            permalink_url="https://app.asana.com/0/0/iso_001",
            modified_at=datetime(2026, 3, 15, 10, 30, 0, tzinfo=UTC),
            due_on="2026-03-15",
            due_at="2026-03-15T17:00:00.000Z",
            start_on=None,
        )
        result = SyncEngine._asana_task_to_dict(task)

        # modified_at should be an ISO string
        assert isinstance(result["modified_at"], str)
        parsed = datetime.fromisoformat(result["modified_at"])
        assert parsed.tzinfo is not None
        assert parsed.year == 2026

        # due_on remains a date string
        assert result["due_on"] == "2026-03-15"
        # due_at remains as-is (raw string from API)
        assert result["due_at"] == "2026-03-15T17:00:00.000Z"

    def test_detect_changes_output_iso_format(
        self, engine: SyncEngine, database: Database
    ) -> None:
        """detect_changes output uses ISO format for all date fields."""
        ts = datetime(2026, 8, 20, 14, 0, 0, tzinfo=UTC)
        _insert_snapshot(
            database,
            gid="iso_dc_001",
            completed=False,
            due_on="2026-08-25",
            start_on="2026-08-15",
            modified_at=ts,
        )
        result = engine.detect_changes(
            [{"gid": "iso_dc_001", "completed": True, "due_on": "2026-08-25", "start_on": "2026-08-15"}]
        )
        assert len(result.pending_changes) == 1
        change = result.pending_changes[0]

        # baseline_modified_at must be valid ISO
        baseline = change["baseline_modified_at"]
        parsed = datetime.fromisoformat(baseline)
        assert parsed.year == 2026

        # current_state.modified_at must be valid ISO
        current_mod = change["current_state"]["modified_at"]
        parsed2 = datetime.fromisoformat(current_mod)
        assert parsed2.year == 2026

    def test_ensure_aware_preserves_non_utc_offset(self) -> None:
        """_ensure_aware does not alter an already-aware non-UTC datetime."""
        eastern = timezone(timedelta(hours=-5))
        dt = datetime(2026, 6, 15, 12, 0, 0, tzinfo=eastern)
        result = SyncEngine._ensure_aware(dt)
        assert result is dt
        assert result.utcoffset() == timedelta(hours=-5)

    def test_upsert_snapshot_modified_at_isoformat_roundtrip(
        self, database: Database, engine: SyncEngine
    ) -> None:
        """modified_at survives upsert → read → isoformat() without loss."""
        task_data: dict[str, Any] = {
            "gid": "iso_rt_001",
            "name": "ISO roundtrip",
            "completed": False,
            "permalink_url": "https://app.asana.com/0/0/iso_rt_001",
            "modified_at": "2026-09-01T09:15:30+00:00",
            "due_on": "2026-09-05",
            "due_at": None,
            "start_on": None,
            "notes": "",
            "memberships": [],
        }

        with database.session() as session:
            engine._upsert_task_snapshot(session, task_data)

        with database.session() as session:
            snap = session.query(TaskSnapshot).filter_by(gid="iso_rt_001").first()
            assert snap is not None
            aware = SyncEngine._ensure_aware(snap.modified_at)
            iso_str = aware.isoformat()
            # Must be parseable and round-trip back
            reparsed = datetime.fromisoformat(iso_str)
            assert reparsed.year == 2026
            assert reparsed.month == 9
            assert reparsed.hour == 9
            assert reparsed.minute == 15
            assert reparsed.second == 30
