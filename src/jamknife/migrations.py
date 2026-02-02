"""Database migration system."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class Migration:
    """Represents a single database migration."""

    def __init__(self, version: str, description: str, up: Callable[[Session], None]):
        """Initialize migration.

        Args:
            version: Migration version (e.g., "001", "002")
            description: Human-readable description
            up: Function that applies the migration
        """
        self.version = version
        self.description = description
        self.up = up


def create_migrations_table(session: Session) -> None:
    """Create the migrations tracking table if it doesn't exist."""
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMP NOT NULL
            )
            """
        )
    )
    session.commit()


def is_migration_applied(session: Session, version: str) -> bool:
    """Check if a migration has already been applied."""
    result = session.execute(
        text("SELECT 1 FROM schema_migrations WHERE version = :version"),
        {"version": version},
    )
    return result.fetchone() is not None


def mark_migration_applied(session: Session, migration: Migration) -> None:
    """Mark a migration as applied."""
    session.execute(
        text(
            """
            INSERT INTO schema_migrations (version, description, applied_at)
            VALUES (:version, :description, :applied_at)
            """
        ),
        {
            "version": migration.version,
            "description": migration.description,
            "applied_at": datetime.now(timezone.utc),
        },
    )
    session.commit()


def run_migrations(session: Session, migrations: list[Migration]) -> None:
    """Run all pending migrations.

    Args:
        session: Database session
        migrations: List of migrations to run (in order)
    """
    # Ensure migrations table exists
    create_migrations_table(session)

    applied_count = 0
    for migration in migrations:
        if is_migration_applied(session, migration.version):
            logger.debug(
                f"Migration {migration.version} ({migration.description}) already applied"
            )
            continue

        logger.info(f"Applying migration {migration.version}: {migration.description}")
        try:
            migration.up(session)
            mark_migration_applied(session, migration)
            applied_count += 1
            logger.info(f"Successfully applied migration {migration.version}")
        except Exception as e:
            logger.error(f"Failed to apply migration {migration.version}: {e}")
            session.rollback()
            raise

    if applied_count > 0:
        logger.info(f"Applied {applied_count} migration(s)")
    else:
        logger.debug("No pending migrations")


# ============================================================================
# Migration definitions
# ============================================================================


def migration_001_add_playlist_schedule(session: Session) -> None:
    """Add enabled and schedule fields to playlists."""
    # Add enabled column (default to true for existing playlists)
    session.execute(
        text(
            """
            ALTER TABLE listenbrainz_playlists
            ADD COLUMN enabled BOOLEAN DEFAULT 1 NOT NULL
            """
        )
    )

    # Add sync_day column for weekly playlists (day of week)
    session.execute(
        text(
            """
            ALTER TABLE listenbrainz_playlists
            ADD COLUMN sync_day VARCHAR(20)
            """
        )
    )

    # Add sync_time column for sync time (HH:MM format)
    session.execute(
        text(
            """
            ALTER TABLE listenbrainz_playlists
            ADD COLUMN sync_time VARCHAR(5)
            """
        )
    )

    # Set default times for existing playlists based on their type
    session.execute(
        text(
            """
            UPDATE listenbrainz_playlists
            SET sync_time = '08:00'
            WHERE sync_time IS NULL
            """
        )
    )

    session.execute(
        text(
            """
            UPDATE listenbrainz_playlists
            SET sync_day = 'monday'
            WHERE is_weekly = 1 AND sync_day IS NULL
            """
        )
    )

    session.commit()


# ============================================================================
# All migrations in order
# ============================================================================

ALL_MIGRATIONS = [
    Migration(
        version="001",
        description="Add playlist scheduling and enable/disable fields",
        up=migration_001_add_playlist_schedule,
    ),
]
