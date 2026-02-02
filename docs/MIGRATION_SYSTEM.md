# Migration System

Jamknife uses an automatic database migration system that tracks and applies database schema changes on application startup.

## How It Works

The migration system provides:

1. **Automatic Execution**: Migrations run automatically when the application starts
2. **Version Tracking**: Each migration is tracked in a `schema_migrations` table
3. **Idempotent**: Safe to run multiple times - already applied migrations are skipped
4. **Sequential Ordering**: Migrations are applied in version order
5. **Transparent Logging**: All migration activity is logged for visibility

## Architecture

### Components

- **`src/jamknife/migrations.py`**: Core migration system
  - `Migration` class: Defines a migration with version, description, and upgrade function
  - `create_migrations_table()`: Creates tracking table if it doesn't exist
  - `is_migration_applied()`: Checks if a migration has been applied
  - `mark_migration_applied()`: Records a migration as applied
  - `run_migrations()`: Executes all pending migrations
  - `ALL_MIGRATIONS`: List of all migrations in version order

- **`src/jamknife/web/app.py`**: Integration point
  - Calls `run_migrations()` during FastAPI lifespan startup
  - Migrations run after database initialization

### Migration Tracking

Migrations are tracked in the `schema_migrations` table:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(10) PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

## Creating a New Migration

1. **Define the migration in `src/jamknife/migrations.py`**:

```python
def migration_002_up(session: Session) -> None:
    """Add new column example."""
    logger.info("Running migration 002: Add example column")
    
    session.execute(
        text(
            """
            ALTER TABLE some_table
            ADD COLUMN new_column VARCHAR(100)
            """
        )
    )
    session.commit()

migration_002 = Migration(
    version="002",
    description="Add example column to some_table",
    up=migration_002_up,
)
```

2. **Add to `ALL_MIGRATIONS` list**:

```python
ALL_MIGRATIONS = [
    migration_001,
    migration_002,  # Add your new migration here
]
```

3. **Test the migration**:

```python
def test_migration_002_adds_column():
    """Test that migration 002 adds the column."""
    engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(bind=engine)
    session = TestSessionLocal()
    
    # Setup: create table without new column
    session.execute(text("CREATE TABLE some_table (id INTEGER PRIMARY KEY)"))
    session.commit()
    
    # Run migration
    run_migrations(session, [migration_002])
    
    # Verify
    result = session.execute(text("PRAGMA table_info(some_table)"))
    columns = {row[1] for row in result.fetchall()}
    assert "new_column" in columns
    
    session.close()
```

## Existing Migrations

### Migration 001: Playlist Scheduling

**Version**: `001`  
**Description**: "Add playlist scheduling fields"

Adds three columns to the `listenbrainz_playlists` table:

- `enabled` (BOOLEAN, default TRUE): Enable/disable playlist syncing
- `sync_day` (VARCHAR(20), nullable): Day of week for weekly playlists
- `sync_time` (VARCHAR(5), nullable): Time in HH:MM format for sync

Sets default values:
- `sync_time = '08:00'` for all existing playlists
- `sync_day = 'monday'` for all weekly playlists

## Best Practices

1. **Version Numbers**: Use sequential zero-padded numbers (001, 002, 003, etc.)
2. **Descriptions**: Write clear, concise descriptions of what the migration does
3. **Idempotency**: Use `IF NOT EXISTS` or check before adding columns when possible
4. **Transactions**: Commit after each significant change to ensure data consistency
5. **Logging**: Log all significant actions within the migration
6. **Testing**: Always write tests for new migrations
7. **SQLite Limitations**: Remember SQLite doesn't support DROP COLUMN or ALTER COLUMN
8. **Data Migrations**: When changing data, handle NULL values and edge cases

## SQLite Constraints

SQLite has limited ALTER TABLE support:

- ✅ Can ADD COLUMN
- ❌ Cannot DROP COLUMN
- ❌ Cannot ALTER COLUMN type
- ❌ Cannot ADD CONSTRAINT after creation

For complex schema changes, use the [recreate table pattern](https://www.sqlite.org/lang_altertable.html#making_other_kinds_of_table_schema_changes):

```python
def migration_complex_up(session: Session) -> None:
    """Recreate table with new schema."""
    # Create new table
    session.execute(text("CREATE TABLE table_new (...)"))
    
    # Copy data
    session.execute(text("INSERT INTO table_new SELECT ... FROM table"))
    
    # Swap tables
    session.execute(text("DROP TABLE table"))
    session.execute(text("ALTER TABLE table_new RENAME TO table"))
    
    session.commit()
```

## Troubleshooting

### Migration Won't Run

Check if it's already applied:

```sql
SELECT * FROM schema_migrations;
```

### Reset Migrations (Development Only)

**⚠️ WARNING: This will delete all migration history!**

```sql
DROP TABLE schema_migrations;
```

Then restart the application to reapply all migrations.

### Migration Failed

1. Check application logs for error details
2. Manually inspect database schema: `sqlite3 data/jamknife.db ".schema"`
3. Check `schema_migrations` table to see what applied
4. Fix the migration code and remove the failed version from `schema_migrations`

## Testing

The migration system has comprehensive tests in `tests/test_migrations.py`:

- `test_create_migrations_table`: Tracking table creation
- `test_is_migration_applied`: Version checking
- `test_mark_migration_applied`: Recording migrations
- `test_run_migrations_applies_pending`: Applying new migrations
- `test_run_migrations_skips_applied`: Idempotency
- `test_run_migrations_in_order`: Sequential execution
- `test_all_migrations_defined`: Migration list validation
- `test_migration_001_adds_playlist_fields`: Specific migration tests

Run tests with:

```bash
python -m pytest tests/test_migrations.py -v
```

## Production Considerations

1. **Backups**: Always backup the database before deploying migrations
2. **Zero-Downtime**: Migrations run at startup, causing brief downtime
3. **Rollbacks**: Manual only - no automatic rollback support
4. **Monitoring**: Check logs after deployment to ensure migrations succeeded
5. **Testing**: Test migrations on a copy of production data first

## Manual Intervention

If you need to manually mark a migration as applied (e.g., after manual fix):

```python
from jamknife.database import SessionLocal
from jamknife.migrations import mark_migration_applied, migration_001

session = SessionLocal()
mark_migration_applied(session, migration_001)
session.close()
```

## Future Enhancements

Potential improvements to consider:

- **Down migrations**: Add rollback support
- **Migration verification**: Checksums to detect modified migrations
- **Dry-run mode**: Preview migrations without applying
- **Migration locks**: Prevent concurrent migrations
- **External migration tool**: CLI for migration management
- **Migration history**: Keep detailed logs of all changes
