"""Tests for database migration system."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from jamknife.database import Base
from jamknife.migrations import (
    ALL_MIGRATIONS,
    Migration,
    create_migrations_table,
    is_migration_applied,
    mark_migration_applied,
    run_migrations,
)


@pytest.fixture
def db_session():
    """Create a test database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)
    session = TestSessionLocal()
    yield session
    session.close()


def test_create_migrations_table(db_session):
    """Test creating the migrations tracking table."""
    create_migrations_table(db_session)

    # Verify table exists
    result = db_session.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
    )
    assert result.fetchone() is not None


def test_is_migration_applied(db_session):
    """Test checking if a migration is applied."""
    create_migrations_table(db_session)

    # Should return False for unapplied migration
    assert not is_migration_applied(db_session, "001")

    # Apply a dummy migration
    test_migration = Migration(
        version="001",
        description="Test migration",
        up=lambda s: None,
    )
    mark_migration_applied(db_session, test_migration)

    # Should return True now
    assert is_migration_applied(db_session, "001")


def test_mark_migration_applied(db_session):
    """Test marking a migration as applied."""
    create_migrations_table(db_session)

    test_migration = Migration(
        version="001",
        description="Test migration",
        up=lambda s: None,
    )

    mark_migration_applied(db_session, test_migration)

    # Verify it was recorded
    result = db_session.execute(
        text("SELECT version, description FROM schema_migrations WHERE version = :v"),
        {"v": "001"},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "001"
    assert row[1] == "Test migration"


def test_run_migrations_applies_pending(db_session):
    """Test that run_migrations applies pending migrations."""
    applied_migrations = []

    def migration_func(session):
        applied_migrations.append("test")

    test_migrations = [
        Migration(
            version="001",
            description="Test migration 1",
            up=migration_func,
        ),
    ]

    run_migrations(db_session, test_migrations)

    # Verify migration was applied
    assert len(applied_migrations) == 1
    assert is_migration_applied(db_session, "001")


def test_run_migrations_skips_applied(db_session):
    """Test that run_migrations skips already applied migrations."""
    applied_count = []

    def migration_func(session):
        applied_count.append(1)

    test_migrations = [
        Migration(
            version="001",
            description="Test migration",
            up=migration_func,
        ),
    ]

    # Run migrations twice
    run_migrations(db_session, test_migrations)
    run_migrations(db_session, test_migrations)

    # Should only have been applied once
    assert len(applied_count) == 1


def test_run_migrations_in_order(db_session):
    """Test that migrations run in order."""
    execution_order = []

    def make_migration_func(n):
        def func(session):
            execution_order.append(n)

        return func

    test_migrations = [
        Migration(version="001", description="First", up=make_migration_func(1)),
        Migration(version="002", description="Second", up=make_migration_func(2)),
        Migration(version="003", description="Third", up=make_migration_func(3)),
    ]

    run_migrations(db_session, test_migrations)

    assert execution_order == [1, 2, 3]


def test_all_migrations_defined():
    """Test that ALL_MIGRATIONS contains migrations."""
    assert len(ALL_MIGRATIONS) > 0
    assert all(isinstance(m, Migration) for m in ALL_MIGRATIONS)


def test_migration_001_adds_playlist_fields():
    """Test that migration 001 adds the correct fields."""
    # Create a fresh database without the migration
    engine = create_engine("sqlite:///:memory:")
    # Don't use Base.metadata.create_all - we want a clean table
    TestSessionLocal = sessionmaker(bind=engine)
    session = TestSessionLocal()

    # Create playlists table without new fields
    session.execute(
        text(
            """
            CREATE TABLE listenbrainz_playlists (
                id INTEGER PRIMARY KEY,
                mbid VARCHAR(36) NOT NULL,
                name VARCHAR(255) NOT NULL,
                creator VARCHAR(255) NOT NULL,
                created_for VARCHAR(255),
                is_daily BOOLEAN DEFAULT 0,
                is_weekly BOOLEAN DEFAULT 0,
                last_synced_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
    )
    session.commit()

    # Run migration 001
    run_migrations(session, [ALL_MIGRATIONS[0]])

    # Verify new columns exist
    result = session.execute(text("PRAGMA table_info(listenbrainz_playlists)"))
    columns = {row[1] for row in result.fetchall()}

    assert "enabled" in columns
    assert "sync_day" in columns
    assert "sync_time" in columns

    session.close()
