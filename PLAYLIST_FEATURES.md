# Playlist Management Features - Summary

## What's New

You can now:
1. **Enable/disable individual playlists** - Control which playlists sync
2. **Set custom sync schedules** - Choose when each playlist syncs
3. **Trigger manual refresh** - Discover new playlists from ListenBrainz on demand
4. **Configure defaults** - Set default behavior for new playlists via environment variables

## Quick Start

### 1. Set Default Preferences (Optional)

Add to your Docker Compose or environment:

```yaml
environment:
  # Daily Jams - sync every day at 8am
  DAILY_JAM_ENABLED: "true"
  DAILY_JAM_TIME: "08:00"
  
  # Weekly Jams - sync every Monday at 8am  
  WEEKLY_JAM_ENABLED: "true"
  WEEKLY_JAM_DAY: "monday"
  WEEKLY_JAM_TIME: "08:00"
  
  # Weekly Explore - sync every Monday at 8am
  WEEKLY_EXPLORE_ENABLED: "true"
  WEEKLY_EXPLORE_DAY: "monday"
  WEEKLY_EXPLORE_TIME: "08:00"
```

### 2. Discover Playlists

```bash
# Refresh and auto-add new playlists from ListenBrainz
curl -X POST http://localhost:8080/api/playlists/refresh
```

### 3. Manage Individual Playlists

```bash
# Disable a playlist
curl -X PATCH http://localhost:8080/api/playlists/1 \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Change sync schedule to Friday at 6pm
curl -X PATCH http://localhost:8080/api/playlists/2 \
  -H "Content-Type: application/json" \
  -d '{"sync_day": "friday", "sync_time": "18:00"}'
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/playlists/refresh` | Discover and add new playlists |
| `PATCH` | `/api/playlists/{id}` | Update playlist settings |
| `GET` | `/api/playlists` | List all playlists (now includes schedule) |

## Database Changes

New fields added to `listenbrainz_playlists` table:
- `enabled` (BOOLEAN) - Whether playlist is active
- `sync_day` (VARCHAR) - Day of week for weekly playlists  
- `sync_time` (VARCHAR) - Time in HH:MM format

### Automatic Migration

**No manual action required!** Migrations run automatically on application startup.

The migration system:
- Detects and applies any pending migrations automatically
- Tracks applied migrations in `schema_migrations` table
- Is safe to run multiple times (won't re-apply migrations)
- Logs all migration activity

Just start (or restart) your Jamknife instance and migrations will be applied.

## Files Changed

- `src/jamknife/config.py` - Added playlist scheduling configuration
- `src/jamknife/database.py` - Added enabled/schedule fields to model
- `src/jamknife/web/app.py` - Added refresh and update endpoints
- `Dockerfile` - Added new environment variables
- `migrations/add_playlist_schedule.sql` - Database migration script
- `docs/PLAYLIST_SCHEDULING.md` - Comprehensive documentation

## Example Workflow

1. **Initial Setup**: Set your preferred defaults in environment variables
2. **Discover**: Call `/api/playlists/refresh` to find available playlists
3. **Review**: Check `/api/playlists` to see what was discovered
4. **Customize**: Use `PATCH /api/playlists/{id}` to adjust individual playlists
5. **Maintain**: Periodically refresh to discover new playlists

## Notes

- Disabled playlists stay in database but won't sync
- Times use 24-hour format (e.g., "14:00" = 2pm)
- Days are case-insensitive (Monday/monday/MONDAY all work)
- Only enabled playlists will be synced automatically
- Manual sync still works regardless of schedule settings

See `docs/PLAYLIST_SCHEDULING.md` for complete documentation.
