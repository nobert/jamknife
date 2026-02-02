# Jamknife

Jamknife syncs ListenBrainz playlists to your Plex server by automatically downloading missing tracks from YouTube Music via Yubal.

## Overview

Jamknife connects three services:

1. **ListenBrainz** - Retrieves daily/weekly generated playlists and track metadata
2. **YouTube Music** - Resolves track information to album URLs for downloading
3. **Yubal** - Downloads albums from YouTube Music in your preferred audio format
4. **Plex** - Searches your library and creates synced playlists

The application tracks all operations in a local SQLite database and provides a web interface for monitoring sync jobs, viewing track matches, and managing playlists.

## Requirements

- Python 3.11+
- A [ListenBrainz](https://listenbrainz.org) account with generated playlists
- A [Plex Media Server](https://www.plex.tv) with a music library
- A running [Yubal](https://github.com/your-org/yubal) instance for downloading

## Installation

### Using Docker (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/jamknife/jamknife.git
   cd jamknife
   ```

2. Create a `.env` file with your configuration:
   ```bash
   LISTENBRAINZ_USERNAME=your_username
   LISTENBRAINZ_TOKEN=your_token  # Optional, for private playlists
   PLEX_URL=http://your-plex-server:32400
   PLEX_TOKEN=your_plex_token
   PLEX_MUSIC_LIBRARY=Music
   YUBAL_URL=http://yubal:8000
   DOWNLOADS_PATH=/path/to/plex/music/downloads
   ```

3. Start the services:
   ```bash
   docker compose up -d
   ```

4. Access the web interface at `http://localhost:8080`

### Manual Installation

1. Clone and install:
   ```bash
   git clone https://github.com/jamknife/jamknife.git
   cd jamknife
   pip install .
   ```

2. Set environment variables (see Configuration section)

3. Run the application:
   ```bash
   jamknife
   ```

## Configuration

All configuration is done via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LISTENBRAINZ_USERNAME` | Yes | - | Your ListenBrainz username |
| `LISTENBRAINZ_TOKEN` | No | - | API token for private playlists |
| `PLEX_URL` | Yes | - | URL of your Plex server |
| `PLEX_TOKEN` | Yes | - | Plex authentication token |
| `PLEX_MUSIC_LIBRARY` | No | `Music` | Name of your Plex music library |
| `YUBAL_URL` | Yes | - | URL of your Yubal instance |
| `DATA_DIR` | No | `/data` | Directory for SQLite database |
| `DOWNLOADS_DIR` | No | `/downloads` | Directory for downloaded albums |
| `WEB_HOST` | No | `0.0.0.0` | Web server bind address |
| `WEB_PORT` | No | `8080` | Web server port |

### Getting Your Plex Token

1. Sign in to Plex Web App
2. Browse to any media item
3. Click the three dots menu and select "Get Info"
4. Click "View XML"
5. Look for `X-Plex-Token` in the URL

### Getting Your ListenBrainz Token

1. Go to [ListenBrainz](https://listenbrainz.org)
2. Navigate to Settings > API
3. Copy your User Token

## Usage

### Web Interface

The web interface provides:

- **Dashboard** - Overview of configuration status, recent sync jobs, and active downloads
- **Playlists** - Discover new ListenBrainz playlists, view tracked playlists, and trigger syncs
- **Playlist Detail** - View individual playlist tracks and sync history
- **Sync Job Detail** - Monitor sync progress, view track matches and download status
- **Downloads** - Track album download jobs submitted to Yubal

### Typical Workflow

1. Open the Playlists page and click "Discover Playlists" to find new ListenBrainz playlists
2. Select playlists you want to track (daily jams, weekly jams, etc.)
3. Click "Sync" on a playlist to start the sync process
4. The sync job will:
   - Fetch the playlist tracks from ListenBrainz
   - Search your Plex library for each track
   - For missing tracks, search YouTube Music for the album
   - Submit album download jobs to Yubal
   - Wait for downloads to complete
   - Refresh your Plex library
   - Create or update the playlist in Plex with all found tracks

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Application status and configuration |
| `/api/playlists` | GET | List tracked playlists |
| `/api/playlists/discover` | POST | Discover new ListenBrainz playlists |
| `/api/playlists/{id}` | GET | Get playlist details |
| `/api/playlists/{id}` | DELETE | Stop tracking a playlist |
| `/api/playlists/{id}/sync` | POST | Start a sync job |
| `/api/sync-jobs` | GET | List sync jobs |
| `/api/sync-jobs/{id}` | GET | Get sync job details |
| `/api/downloads` | GET | List album downloads |

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  ListenBrainz   │     │  YouTube Music  │     │      Yubal      │
│     API         │     │      API        │     │     Service     │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Jamknife                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ LB Client    │  │ YTMusic      │  │ Yubal Client │          │
│  │              │  │ Resolver     │  │              │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                 │                   │
│         └────────┬────────┴────────┬────────┘                   │
│                  ▼                 ▼                            │
│         ┌──────────────────────────────────┐                   │
│         │        Sync Service              │                   │
│         └──────────────┬───────────────────┘                   │
│                        │                                        │
│         ┌──────────────┴───────────────────┐                   │
│         │          SQLite DB               │                   │
│         └──────────────────────────────────┘                   │
│                        │                                        │
│         ┌──────────────┴───────────────────┐                   │
│         │       FastAPI Web App            │◄────── Web UI     │
│         └──────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
              ┌─────────────────┐
              │   Plex Server   │
              │   (plexapi)     │
              └─────────────────┘
```

## Database Schema

Jamknife uses SQLite with the following tables:

- **listenbrainz_playlists** - Tracked playlists from ListenBrainz
- **playlist_sync_jobs** - Sync job records with status and statistics
- **track_matches** - Per-track match results (found in Plex, downloaded, not found)
- **album_downloads** - Yubal download job tracking
- **mbid_plex_mappings** - Cache of MusicBrainz ID to Plex rating key mappings

## Development

### Setup

```bash
git clone https://github.com/jamknife/jamknife.git
cd jamknife
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

### Linting

```bash
ruff check src/
ruff format src/
mypy src/jamknife
```

### Building

```bash
pip install build
python -m build
```

## Troubleshooting

### Sync jobs stuck in "downloading" status

Check that Yubal is running and accessible. Verify the `YUBAL_URL` is correct and that Jamknife can reach the Yubal API.

### Tracks not found in Plex after download

1. Ensure the `DOWNLOADS_DIR` is the same directory that Plex monitors
2. Check that Plex library auto-scan is enabled, or trigger a manual scan
3. Verify downloaded files have correct metadata for Plex to match

### No playlists found during discovery

1. Verify your `LISTENBRAINZ_USERNAME` is correct
2. Check that you have "Created For You" playlists on ListenBrainz (daily jams, weekly jams)
3. If playlists are private, ensure `LISTENBRAINZ_TOKEN` is set

### YouTube Music album not found

The resolver uses multiple strategies to find albums. If a track consistently fails:
1. The track may not be available on YouTube Music
2. The metadata may differ significantly between ListenBrainz and YouTube Music
3. Check the sync job details for specific error messages
