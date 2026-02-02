# Playlist Scheduling and Management

## Overview

Jamknife now supports enabling/disabling individual playlists and controlling when they sync. You can configure default settings via environment variables and manage individual playlists through the API.

## Configuration

### Environment Variables

Control default behavior for discovered playlists:

```bash
# Daily Jam Settings
DAILY_JAM_ENABLED=true          # Enable/disable daily jam playlists (default: true)
DAILY_JAM_TIME=08:00           # Time to sync daily playlists in HH:MM format

# Weekly Jam Settings  
WEEKLY_JAM_ENABLED=true         # Enable/disable weekly jam playlists (default: true)
WEEKLY_JAM_DAY=monday          # Day of week to sync (monday-sunday)
WEEKLY_JAM_TIME=08:00          # Time to sync in HH:MM format

# Weekly Exploration Settings
WEEKLY_EXPLORE_ENABLED=true    # Enable/disable weekly exploration playlists (default: true)
WEEKLY_EXPLORE_DAY=monday      # Day of week to sync (monday-sunday)
WEEKLY_EXPLORE_TIME=08:00      # Time to sync in HH:MM format
```

## API Endpoints

### Refresh Playlists

Discover and add new playlists from ListenBrainz:

```http
POST /api/playlists/refresh
```

**Response:**
```json
{
  "status": "refreshed",
  "added": 3,
  "discovered": 3
}
```

This endpoint:
- Discovers all available daily/weekly playlists from ListenBrainz
- Automatically adds any new playlists not already tracked
- Applies default enabled/schedule settings from environment variables
- Returns count of newly added playlists

### Update Playlist Settings

Enable/disable a playlist or change its sync schedule:

```http
PATCH /api/playlists/{playlist_id}
Content-Type: application/json

{
  "enabled": true,
  "sync_day": "tuesday",
  "sync_time": "14:30"
}
```

**Parameters:**
- `enabled` (optional): `true` or `false` to enable/disable the playlist
- `sync_day` (optional): Day of week for weekly playlists (monday-sunday, case-insensitive)
- `sync_time` (optional): Time in HH:MM format (24-hour, e.g., "14:30" for 2:30 PM)

**Response:**
```json
{
  "id": 1,
  "mbid": "abc123...",
  "name": "Weekly Jams",
  "creator": "ListenBrainz",
  "created_for": "username",
  "is_daily": false,
  "is_weekly": true,
  "enabled": true,
  "sync_day": "tuesday",
  "sync_time": "14:30",
  "last_synced_at": "2026-02-01T10:00:00Z",
  "created_at": "2026-01-15T08:00:00Z"
}
```

### List Playlists

The existing list endpoint now includes the new fields:

```http
GET /api/playlists
```

**Response:**
```json
[
  {
    "id": 1,
    "mbid": "abc123...",
    "name": "Daily Jams",
    "enabled": true,
    "sync_time": "08:00",
    "sync_day": null,
    "is_daily": true,
    "is_weekly": false,
    ...
  },
  {
    "id": 2,
    "name": "Weekly Jams",
    "enabled": false,
    "sync_time": "09:00",
    "sync_day": "monday",
    "is_daily": false,
    "is_weekly": true,
    ...
  }
]
```

## Usage Examples

### Using cURL

**Refresh playlists from ListenBrainz:**
```bash
curl -X POST http://localhost:8080/api/playlists/refresh
```

**Disable a playlist:**
```bash
curl -X PATCH http://localhost:8080/api/playlists/1 \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

**Change sync schedule:**
```bash
curl -X PATCH http://localhost:8080/api/playlists/2 \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "sync_day": "friday",
    "sync_time": "18:00"
  }'
```

### Using Python

```python
import httpx

base_url = "http://localhost:8080"

# Refresh playlists
response = httpx.post(f"{base_url}/api/playlists/refresh")
print(response.json())

# Update playlist settings
response = httpx.patch(
    f"{base_url}/api/playlists/1",
    json={
        "enabled": True,
        "sync_time": "07:00"
    }
)
print(response.json())
```

## Database Migration

**Database migrations are now automatic!** When you start Jamknife, it will automatically detect and apply any pending migrations.

The migration system:
- Tracks which migrations have been applied in a `schema_migrations` table
- Only runs migrations that haven't been applied yet
- Is safe to run multiple times (idempotent)
- Runs automatically on application startup

### Manual Migration (if needed)

If you need to manually check or apply migrations, you can connect to the database:

```bash
# Check applied migrations
sqlite3 /data/jamknife.db "SELECT * FROM schema_migrations;"

# The application will automatically apply pending migrations on next startup
```

### Migration History

| Version | Description | Status |
|---------|-------------|--------|
| 001 | Add playlist scheduling and enable/disable fields | Auto-applied on startup |

## Workflow

### Initial Setup

1. Start Jamknife with your preferred default settings in environment variables
2. Call `/api/playlists/refresh` to discover available playlists
3. Newly discovered playlists will use the default settings from your environment

### Managing Playlists

1. List playlists with `GET /api/playlists` to see all tracked playlists
2. Disable unwanted playlists with `PATCH /api/playlists/{id}` setting `enabled: false`
3. Adjust sync schedules for individual playlists as needed
4. Re-run `/api/playlists/refresh` periodically to discover new playlists

### Sync Behavior

- Only playlists with `enabled: true` will be synced
- Daily playlists sync at their configured `sync_time` each day
- Weekly playlists sync at their configured `sync_day` and `sync_time` each week
- The sync scheduler respects these settings when running automatic syncs

## Notes

- All times are in 24-hour format (HH:MM)
- Days of week are case-insensitive (Monday, monday, MONDAY all work)
- Valid days: monday, tuesday, wednesday, thursday, friday, saturday, sunday
- Time validation ensures hours are 0-23 and minutes are 0-59
- Disabled playlists remain in the database but won't be synced
- The `sync_day` field is only relevant for weekly playlists
