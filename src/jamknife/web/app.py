"""FastAPI web application for Jamknife."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from jamknife.config import get_config
from jamknife.database import (
    AlbumDownload,
    DownloadStatus,
    ListenBrainzPlaylist,
    PlaylistSyncJob,
    SyncStatus,
    init_database,
)
from jamknife.services.sync import PlaylistSyncService

logger = logging.getLogger(__name__)

# Global state
_session_factory = None
_sync_service = None


def get_session():
    """Get a database session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized")
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_sync_service() -> PlaylistSyncService:
    """Get the sync service singleton."""
    if _sync_service is None:
        raise RuntimeError("Sync service not initialized")
    return _sync_service


SessionDep = Annotated[Session, Depends(get_session)]
SyncServiceDep = Annotated[PlaylistSyncService, Depends(get_sync_service)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global _session_factory, _sync_service

    config = get_config()

    # Validate configuration
    errors = config.validate()
    if errors:
        for error in errors:
            logger.error("Configuration error: %s", error)

    # Initialize database
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _session_factory = init_database(config.db_path)

    # Run database migrations
    from jamknife.migrations import ALL_MIGRATIONS, run_migrations

    with _session_factory() as session:
        run_migrations(session, ALL_MIGRATIONS)

    # Initialize sync service
    _sync_service = PlaylistSyncService(config, _session_factory)

    logger.info("Jamknife started")
    yield

    logger.info("Jamknife shutting down")


app = FastAPI(
    title="Jamknife",
    description="ListenBrainz to Plex playlist sync with YouTube Music downloads",
    version="0.1.0",
    lifespan=lifespan,
)

# Templates setup (will be configured in main)
templates = None


# ============================================================================
# Pydantic models for API
# ============================================================================


class PlaylistResponse(BaseModel):
    """Response model for a playlist."""

    id: int
    mbid: str
    name: str
    creator: str
    created_for: str | None
    is_daily: bool
    is_weekly: bool
    enabled: bool
    sync_day: str | None
    sync_time: str | None
    last_synced_at: datetime | None
    created_at: datetime

    class ConfigDict:
        from_attributes = True


class SyncJobResponse(BaseModel):
    """Response model for a sync job."""

    id: int
    playlist_id: int
    playlist_name: str
    status: str
    error_message: str | None
    tracks_total: int
    tracks_matched: int
    tracks_missing: int
    plex_playlist_key: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    class ConfigDict:
        from_attributes = True


class AlbumDownloadResponse(BaseModel):
    """Response model for an album download."""

    id: int
    ytmusic_album_id: str
    album_name: str
    artist_name: str
    status: str
    progress: float
    error_message: str | None
    queued_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    class ConfigDict:
        from_attributes = True


class DiscoveredPlaylistResponse(BaseModel):
    """Response model for a discovered playlist."""

    mbid: str
    name: str
    creator: str
    created_for: str | None
    is_daily: bool
    is_weekly: bool


class AddPlaylistRequest(BaseModel):
    """Request model for adding a playlist."""

    mbid: str


class UpdatePlaylistRequest(BaseModel):
    """Request model for updating a playlist."""

    enabled: bool | None = None
    sync_day: str | None = None
    sync_time: str | None = None


class SyncJobRequest(BaseModel):
    """Request model for creating a sync job."""

    playlist_id: int


class StatusResponse(BaseModel):
    """Response model for status endpoint."""

    status: str
    listenbrainz_configured: bool
    plex_configured: bool
    yubal_configured: bool
    playlists_tracked: int
    active_sync_jobs: int
    pending_downloads: int


# ============================================================================
# API Routes
# ============================================================================


@app.get("/api/status")
async def get_status(session: SessionDep) -> StatusResponse:
    """Get application status and configuration."""
    config = get_config()

    playlists_count = session.query(ListenBrainzPlaylist).count()
    active_jobs = (
        session.query(PlaylistSyncJob)
        .filter(
            PlaylistSyncJob.status.notin_([SyncStatus.COMPLETED, SyncStatus.FAILED])
        )
        .count()
    )
    pending_downloads = (
        session.query(AlbumDownload)
        .filter(
            AlbumDownload.status.in_(
                [
                    DownloadStatus.PENDING,
                    DownloadStatus.QUEUED,
                    DownloadStatus.DOWNLOADING,
                ]
            )
        )
        .count()
    )

    return StatusResponse(
        status="healthy",
        listenbrainz_configured=bool(config.listenbrainz_username),
        plex_configured=bool(config.plex_token),
        yubal_configured=bool(config.yubal_url),
        playlists_tracked=playlists_count,
        active_sync_jobs=active_jobs,
        pending_downloads=pending_downloads,
    )


@app.get("/api/playlists")
async def list_playlists(session: SessionDep) -> list[PlaylistResponse]:
    """List all tracked playlists."""
    playlists = (
        session.query(ListenBrainzPlaylist)
        .order_by(ListenBrainzPlaylist.created_at.desc())
        .all()
    )
    return [PlaylistResponse.model_validate(p, from_attributes=True) for p in playlists]


@app.get("/api/playlists/discover")
async def discover_playlists(
    sync_service: SyncServiceDep,
) -> list[DiscoveredPlaylistResponse]:
    """Discover new daily/weekly playlists from ListenBrainz."""
    playlists = sync_service.discover_playlists()
    return [
        DiscoveredPlaylistResponse(
            mbid=p.mbid,
            name=p.name,
            creator=p.creator,
            created_for=p.created_for,
            is_daily=p.is_daily,
            is_weekly=p.is_weekly,
        )
        for p in playlists
    ]


@app.post("/api/playlists")
async def add_playlist(
    request: AddPlaylistRequest,
    sync_service: SyncServiceDep,
    session: SessionDep,
) -> PlaylistResponse:
    """Add a discovered playlist to tracking."""
    # Check if already exists
    existing = session.query(ListenBrainzPlaylist).filter_by(mbid=request.mbid).first()
    if existing:
        return PlaylistResponse.model_validate(existing, from_attributes=True)

    # Discover and find the playlist
    discovered = sync_service.discover_playlists()
    playlist = next((p for p in discovered if p.mbid == request.mbid), None)

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist = sync_service.add_playlist(playlist)
    return PlaylistResponse.model_validate(playlist, from_attributes=True)


@app.patch("/api/playlists/{playlist_id}")
async def update_playlist(
    playlist_id: int,
    request: UpdatePlaylistRequest,
    session: SessionDep,
) -> PlaylistResponse:
    """Update playlist settings (enabled, schedule)."""
    playlist = session.query(ListenBrainzPlaylist).get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if request.enabled is not None:
        playlist.enabled = request.enabled
    if request.sync_day is not None:
        # Validate day of week
        valid_days = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        if request.sync_day.lower() not in valid_days:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sync_day. Must be one of: {', '.join(valid_days)}",
            )
        playlist.sync_day = request.sync_day.lower()
    if request.sync_time is not None:
        # Validate time format HH:MM
        try:
            hour, minute = request.sync_time.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except (ValueError, AttributeError) as e:
            raise HTTPException(
                status_code=400,
                detail="Invalid sync_time. Must be in HH:MM format (e.g., 08:00)",
            ) from e
        playlist.sync_time = request.sync_time

    session.commit()
    session.refresh(playlist)
    return PlaylistResponse.model_validate(playlist, from_attributes=True)


@app.post("/api/playlists/refresh")
async def refresh_playlists(
    sync_service: SyncServiceDep,
    session: SessionDep,
) -> dict:
    """Discover and add any new playlists from ListenBrainz."""
    discovered = sync_service.discover_playlists()
    added_count = 0

    config = get_config()

    for playlist in discovered:
        # Set default enabled/schedule based on playlist type and config
        if playlist.is_daily:
            playlist.enabled = config.daily_jam_enabled
            playlist.sync_time = config.daily_jam_time
        elif playlist.is_weekly:
            # Determine if it's explore or jam based on name
            is_explore = (
                "exploration" in playlist.name.lower()
                or "explore" in playlist.name.lower()
            )
            if is_explore:
                playlist.enabled = config.weekly_explore_enabled
                playlist.sync_day = config.weekly_explore_day
                playlist.sync_time = config.weekly_explore_time
            else:
                playlist.enabled = config.weekly_jam_enabled
                playlist.sync_day = config.weekly_jam_day
                playlist.sync_time = config.weekly_jam_time

        playlist = sync_service.add_playlist(playlist)
        added_count += 1

    return {"status": "refreshed", "added": added_count, "discovered": len(discovered)}


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, session: SessionDep) -> dict:
    """Remove a playlist from tracking."""
    playlist = session.query(ListenBrainzPlaylist).get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    session.delete(playlist)
    session.commit()
    return {"status": "deleted"}


@app.get("/api/sync-jobs")
async def list_sync_jobs(
    session: SessionDep, playlist_id: int | None = None, limit: int = 50
) -> list[SyncJobResponse]:
    """List sync jobs, optionally filtered by playlist."""
    query = session.query(PlaylistSyncJob)
    if playlist_id:
        query = query.filter_by(playlist_id=playlist_id)
    jobs = query.order_by(PlaylistSyncJob.created_at.desc()).limit(limit).all()

    return [
        SyncJobResponse(
            id=j.id,
            playlist_id=j.playlist_id,
            playlist_name=j.playlist.name,
            status=j.status.value,
            error_message=j.error_message,
            tracks_total=j.tracks_total,
            tracks_matched=j.tracks_matched,
            tracks_missing=j.tracks_missing,
            plex_playlist_key=j.plex_playlist_key,
            started_at=j.started_at,
            completed_at=j.completed_at,
            created_at=j.created_at,
        )
        for j in jobs
    ]


@app.post("/api/sync-jobs")
async def create_sync_job(
    request: SyncJobRequest,
    sync_service: SyncServiceDep,
    background_tasks: BackgroundTasks,
    session: SessionDep,
) -> SyncJobResponse:
    """Create and start a new sync job."""
    playlist = session.query(ListenBrainzPlaylist).get(request.playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # Check for existing active job
    active_job = (
        session.query(PlaylistSyncJob)
        .filter_by(playlist_id=request.playlist_id)
        .filter(
            PlaylistSyncJob.status.notin_([SyncStatus.COMPLETED, SyncStatus.FAILED])
        )
        .first()
    )
    if active_job:
        raise HTTPException(
            status_code=409, detail="A sync job is already running for this playlist"
        )

    job = sync_service.create_sync_job(request.playlist_id)

    # Run sync in background
    background_tasks.add_task(sync_service.run_sync_job, job.id)

    # Re-query the job in this session to get it with relationships
    job = session.query(PlaylistSyncJob).filter_by(id=job.id).first()

    return SyncJobResponse(
        id=job.id,
        playlist_id=job.playlist_id,
        playlist_name=job.playlist.name,
        status=job.status.value,
        error_message=job.error_message,
        tracks_total=job.tracks_total,
        tracks_matched=job.tracks_matched,
        tracks_missing=job.tracks_missing,
        plex_playlist_key=job.plex_playlist_key,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )


@app.get("/api/sync-jobs/{job_id}")
async def get_sync_job(job_id: int, session: SessionDep) -> SyncJobResponse:
    """Get a specific sync job."""
    job = session.query(PlaylistSyncJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")

    return SyncJobResponse(
        id=job.id,
        playlist_id=job.playlist_id,
        playlist_name=job.playlist.name,
        status=job.status.value,
        error_message=job.error_message,
        tracks_total=job.tracks_total,
        tracks_matched=job.tracks_matched,
        tracks_missing=job.tracks_missing,
        plex_playlist_key=job.plex_playlist_key,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )


@app.get("/api/downloads")
async def list_downloads(
    session: SessionDep, status: str | None = None, limit: int = 100
) -> list[AlbumDownloadResponse]:
    """List album downloads, optionally filtered by status."""
    query = session.query(AlbumDownload)
    if status:
        try:
            download_status = DownloadStatus(status)
            query = query.filter_by(status=download_status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status") from None

    downloads = query.order_by(AlbumDownload.created_at.desc()).limit(limit).all()

    return [
        AlbumDownloadResponse(
            id=d.id,
            ytmusic_album_id=d.ytmusic_album_id,
            album_name=d.album_name,
            artist_name=d.artist_name,
            status=d.status.value,
            progress=d.progress,
            error_message=d.error_message,
            queued_at=d.queued_at,
            completed_at=d.completed_at,
            created_at=d.created_at,
        )
        for d in downloads
    ]


# ============================================================================
# Web UI Routes
# ============================================================================


def setup_templates(templates_dir: str):
    """Configure Jinja2 templates."""
    global templates
    templates = Jinja2Templates(directory=templates_dir)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep):
    """Render the main dashboard."""
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    config = get_config()

    playlists = (
        session.query(ListenBrainzPlaylist)
        .order_by(ListenBrainzPlaylist.created_at.desc())
        .all()
    )

    recent_jobs = (
        session.query(PlaylistSyncJob)
        .order_by(PlaylistSyncJob.created_at.desc())
        .limit(10)
        .all()
    )

    active_downloads = (
        session.query(AlbumDownload)
        .filter(
            AlbumDownload.status.in_(
                [
                    DownloadStatus.PENDING,
                    DownloadStatus.QUEUED,
                    DownloadStatus.DOWNLOADING,
                ]
            )
        )
        .all()
    )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "config": config,
            "playlists": playlists,
            "recent_jobs": recent_jobs,
            "active_downloads": active_downloads,
        },
    )


@app.get("/playlists", response_class=HTMLResponse)
async def playlists_page(request: Request, session: SessionDep):
    """Render the playlists page."""
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    config = get_config()

    playlists = (
        session.query(ListenBrainzPlaylist)
        .order_by(ListenBrainzPlaylist.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "playlists.html",
        {
            "request": request,
            "config": config,
            "playlists": playlists,
        },
    )


@app.get("/playlists/{playlist_id}", response_class=HTMLResponse)
async def playlist_detail_page(request: Request, playlist_id: int, session: SessionDep):
    """Render the playlist detail page."""
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    config = get_config()

    playlist = session.query(ListenBrainzPlaylist).get(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    jobs = (
        session.query(PlaylistSyncJob)
        .filter_by(playlist_id=playlist_id)
        .order_by(PlaylistSyncJob.created_at.desc())
        .limit(20)
        .all()
    )

    return templates.TemplateResponse(
        "playlist_detail.html",
        {
            "request": request,
            "config": config,
            "playlist": playlist,
            "jobs": jobs,
        },
    )


@app.get("/downloads", response_class=HTMLResponse)
async def downloads_page(request: Request, session: SessionDep):
    """Render the downloads page."""
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    config = get_config()

    downloads = (
        session.query(AlbumDownload)
        .order_by(AlbumDownload.created_at.desc())
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        "downloads.html",
        {
            "request": request,
            "config": config,
            "downloads": downloads,
        },
    )


@app.get("/sync-jobs/{job_id}", response_class=HTMLResponse)
async def sync_job_detail_page(request: Request, job_id: int, session: SessionDep):
    """Render the sync job detail page."""
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    config = get_config()

    job = session.query(PlaylistSyncJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")

    return templates.TemplateResponse(
        "sync_job_detail.html",
        {
            "request": request,
            "config": config,
            "job": job,
        },
    )
