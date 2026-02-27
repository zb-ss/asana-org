"""Database module for Asana Org Bridge."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from asana_org_bridge.models import Base, SchemaMeta


class Database:
    """Database connection and session management."""

    def __init__(self, db_path: Path, echo: bool = False) -> None:
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
            echo: Whether to echo SQL statements
        """
        self.db_path = db_path
        self.echo = echo
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        """Get or create the SQLAlchemy engine."""
        if self._engine is None:
            # Ensure parent directory exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            url = f"sqlite:///{self.db_path}"
            self._engine = create_engine(
                url,
                echo=self.echo,
                connect_args={"check_same_thread": False},
            )
        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Get or create the session factory."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine,
                expire_on_commit=False,
            )
        return self._session_factory

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Context manager for database sessions."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_all(self) -> None:
        """Create all tables (for testing or fresh init)."""
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        """Drop all tables (for testing)."""
        Base.metadata.drop_all(self.engine)

    def get_schema_version(self) -> str | None:
        """Get the current schema version."""
        try:
            with self.session() as session:
                meta = session.query(SchemaMeta).order_by(SchemaMeta.id.desc()).first()
                return meta.version if meta else None
        except OperationalError:
            # Table doesn't exist yet
            return None

    def set_schema_version(
        self,
        version: str,
        description: str,
    ) -> None:
        """Record a schema migration."""
        with self.session() as session:
            meta = SchemaMeta(
                version=version,
                description=description,
            )
            session.add(meta)


class MigrationManager:
    """Manages database schema migrations."""

    def __init__(self, db: Database) -> None:
        """Initialize migration manager.

        Args:
            db: Database instance
        """
        self.db = db

    MIGRATIONS: dict[str, str] = {
        "001": """
            CREATE TABLE IF NOT EXISTS schema_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version VARCHAR(16) NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                description TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gid VARCHAR(64) NOT NULL,
                permalink_url TEXT NOT NULL,
                name TEXT NOT NULL,
                completed BOOLEAN DEFAULT 0,
                start_on VARCHAR(16),
                due_on VARCHAR(16),
                due_at VARCHAR(32),
                notes TEXT,
                project_gid VARCHAR(64),
                project_name TEXT,
                section_gid VARCHAR(64),
                section_name TEXT,
                memberships_json TEXT,
                stories_json TEXT,
                custom_fields_json TEXT,
                modified_at TIMESTAMP WITH TIME ZONE NOT NULL,
                local_hash VARCHAR(64),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                snapshot_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_tasks_snapshot_gid ON tasks_snapshot(gid);
            CREATE INDEX IF NOT EXISTS ix_tasks_snapshot_gid_snapshot ON tasks_snapshot(gid, snapshot_at);

            CREATE TABLE IF NOT EXISTS org_mirror_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_gid VARCHAR(64) NOT NULL,
                project_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash VARCHAR(64),
                last_synced_at TIMESTAMP WITH TIME ZONE,
                baseline_snapshot_id INTEGER,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_org_mirror_state_project ON org_mirror_state(project_gid);

            CREATE TABLE IF NOT EXISTS pending_mutations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_gid VARCHAR(64) NOT NULL,
                operation VARCHAR(32) NOT NULL,
                payload TEXT NOT NULL,
                idempotency_key VARCHAR(128) NOT NULL UNIQUE,
                status VARCHAR(16) DEFAULT 'pending' NOT NULL,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                error_message TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                applied_at TIMESTAMP WITH TIME ZONE
            );

            CREATE INDEX IF NOT EXISTS ix_pending_mutations_task ON pending_mutations(task_gid);
            CREATE INDEX IF NOT EXISTS ix_pending_mutations_status ON pending_mutations(status);

            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type VARCHAR(16) NOT NULL,
                status VARCHAR(16) NOT NULL,
                tasks_pulled INTEGER DEFAULT 0,
                tasks_updated INTEGER DEFAULT 0,
                mutations_generated INTEGER DEFAULT 0,
                mutations_applied INTEGER DEFAULT 0,
                conflicts_detected INTEGER DEFAULT 0,
                errors TEXT,
                metadata_json TEXT,
                started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP WITH TIME ZONE
            );
        """,
        "002": """
            CREATE TABLE IF NOT EXISTS request_idempotency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key VARCHAR(128) NOT NULL UNIQUE,
                request_hash VARCHAR(64),
                status VARCHAR(16) NOT NULL,
                response_json TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP WITH TIME ZONE
            );

            CREATE INDEX IF NOT EXISTS ix_request_idempotency_key ON request_idempotency(idempotency_key);
        """,
    }

    def get_pending_migrations(self) -> list[str]:
        """Get list of migrations not yet applied."""
        current_version = self.db.get_schema_version()
        pending = []

        for version in sorted(self.MIGRATIONS.keys()):
            if current_version is None or version > current_version:
                pending.append(version)

        return pending

    def apply_migration(self, version: str) -> None:
        """Apply a specific migration.

        Args:
            version: Migration version identifier
        """
        if version not in self.MIGRATIONS:
            raise ValueError(f"Unknown migration version: {version}")

        sql = self.MIGRATIONS[version]

        with self.db.engine.begin() as conn:
            # Execute each statement separately
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))

        self.db.set_schema_version(
            version=version,
            description=f"Migration {version}",
        )

    def migrate(self) -> list[str]:
        """Apply all pending migrations.

        Returns:
            List of applied migration versions
        """
        pending = self.get_pending_migrations()
        applied = []

        for version in pending:
            self.apply_migration(version)
            applied.append(version)

        return applied

    def needs_init(self) -> bool:
        """Check if database needs initialization."""
        return self.db.get_schema_version() is None
