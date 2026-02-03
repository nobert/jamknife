"""FastAPI web application for Jamknife."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from jamknife.clients.yubal import YubalClient
from jamknife.config import get_config
from jamknife.database import (
    AlbumDownload,
    DownloadStatus,
    ListenBrainzPlaylist,
    PlaylistSyncJob,
    SyncStatus,
    TrackMatch,
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


async def submit_pending_downloads(session: Session, config):
    """Submit pending downloads if there's space in the Yubal queue."""

    # Get pending downloads
    pending_downloads = (
        session.query(AlbumDownload)
        .filter(AlbumDownload.status == DownloadStatus.PENDING)
        .filter(AlbumDownload.yubal_job_id.is_(None))
        .order_by(AlbumDownload.created_at)
        .limit(20)  # Limit to avoid overwhelming
        .all()
    )

    if not pending_downloads:
        return

    # Check Yubal queue space
    yubal = YubalClient(config.yubal_url)
    try:
        all_jobs = yubal.list_jobs()
        active_jobs = [j for j in all_jobs if j.status.is_active]
        current_queue_size = len(active_jobs)
        max_queue_size = 18  # Leave buffer
        available_slots = max(0, max_queue_size - current_queue_size)

        if available_slots == 0:
            return

        logger.info(
            "Submitting pending downloads: %d pending, %d slots available",
            len(pending_downloads),
            available_slots,
        )

        submitted = 0
        for download in pending_downloads[:available_slots]:
            try:
                # Check if job already exists for this URL
                existing_job = next(
                    (j for j in all_jobs if j.url == download.ytmusic_album_url),
                    None,
                )
                if existing_job:
                    logger.info(
                        "Found existing job %s for pending download %d",
                        existing_job.id,
                        download.id,
                    )
                    download.yubal_job_id = existing_job.id
                    download.status = DownloadStatus.QUEUED
                    download.queued_at = datetime.now(timezone.utc)
                    submitted += 1
                else:
                    job = yubal.create_job(download.ytmusic_album_url)
                    download.yubal_job_id = job.id
                    download.status = DownloadStatus.QUEUED
                    download.queued_at = datetime.now(timezone.utc)
                    submitted += 1
                    logger.info(
                        "Submitted pending download: %s - %s",
                        download.artist_name,
                        download.album_name,
                    )
            except Exception as e:
                if "409" in str(e) or "Conflict" in str(e):
                    # Queue full, stop trying
                    logger.info("Yubal queue full, stopping pending submission")
                    break
                else:
                    logger.warning(
                        "Failed to submit pending download %d: %s", download.id, e
                    )
                    download.status = DownloadStatus.FAILED
                    download.error_message = str(e)

        if submitted > 0:
            session.commit()
            logger.info("Submitted %d pending download(s)", submitted)

    except Exception as e:
        logger.error("Error submitting pending downloads: %s", e)
    finally:
        yubal.close()


async def check_and_resume_sync_jobs(session: Session):
    """Check for sync jobs with completed downloads and resume them."""
    import asyncio

    # Find sync jobs in DOWNLOADING state
    downloading_jobs = (
        session.query(PlaylistSyncJob)
        .filter(PlaylistSyncJob.status == SyncStatus.DOWNLOADING)
        .all()
    )

    for job in downloading_jobs:
        # Check if all downloads for this job are complete
        track_matches_with_downloads = [
            tm for tm in job.track_matches if tm.album_download_id is not None
        ]

        if not track_matches_with_downloads:
            continue

        all_complete = all(
            tm.album_download.status
            in [DownloadStatus.COMPLETED, DownloadStatus.FAILED]
            for tm in track_matches_with_downloads
        )

        if all_complete:
            logger.info("All downloads complete for sync job %d, resuming...", job.id)
            # Resume the sync job in a background task
            asyncio.create_task(resume_sync_job_async(job.id))


async def resume_sync_job_async(job_id: int):
    """Resume a sync job after downloads complete."""
    import asyncio

    # Run in thread pool since sync service is synchronous
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, resume_sync_job, job_id)


def resume_sync_job(job_id: int):
    """Resume a sync job after downloads complete (synchronous)."""
    if not _sync_service:
        logger.error("Sync service not available to resume job %d", job_id)
        return

    try:
        _sync_service.resume_sync_job_after_downloads(job_id)
    except Exception as e:
        logger.exception("Failed to resume sync job %d: %s", job_id, e)


async def update_download_statuses_loop(config):
    """Background task to periodically update download statuses from Yubal."""
    import asyncio

    from jamknife.clients.yubal import JobStatus as YubalJobStatus

    logger.info("Starting download status update loop")

    while True:
        try:
            await asyncio.sleep(30)  # Update every 30 seconds

            if not _session_factory:
                continue

            session = _session_factory()
            try:
                # Get all active downloads
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
                    .filter(AlbumDownload.yubal_job_id.isnot(None))
                    .all()
                )

                if not active_downloads:
                    continue

                # Fetch all jobs from Yubal once
                yubal = YubalClient(config.yubal_url)
                try:
                    all_jobs = yubal.list_jobs()
                    jobs_by_id = {job.id: job for job in all_jobs}
                finally:
                    yubal.close()

                # Update each download based on job status
                updated_count = 0
                for download in active_downloads:
                    # Check for timeout (2 hours in downloading state)
                    if download.status == DownloadStatus.DOWNLOADING:
                        # Ensure queued_at has timezone info
                        queued_at = download.queued_at
                        if queued_at and queued_at.tzinfo is None:
                            queued_at = queued_at.replace(tzinfo=timezone.utc)

                        if queued_at:
                            time_downloading = (
                                datetime.now(timezone.utc) - queued_at
                            ).total_seconds()
                            if time_downloading > 7200:  # 2 hours
                                logger.warning(
                                    "Download %d timed out after %d seconds",
                                    download.id,
                                    time_downloading,
                                )
                                download.status = DownloadStatus.FAILED
                                download.error_message = f"Download timed out after {time_downloading / 3600:.1f} hours"
                                updated_count += 1
                                continue

                    job = jobs_by_id.get(download.yubal_job_id)
                    if not job:
                        # Job not found in Yubal - might have been deleted or very old
                        if download.status == DownloadStatus.DOWNLOADING:
                            logger.warning(
                                "Job %s not found for download %s - marking as failed",
                                download.yubal_job_id,
                                download.id,
                            )
                            download.status = DownloadStatus.FAILED
                            download.error_message = (
                                "Job not found in Yubal (may have been cleaned up)"
                            )
                            updated_count += 1
                        continue

                    # Update progress
                    download.progress = job.progress

                    # Update status based on job status
                    if job.status == YubalJobStatus.COMPLETED:
                        download.status = DownloadStatus.COMPLETED
                        download.completed_at = datetime.now(timezone.utc)
                        updated_count += 1
                    elif job.status == YubalJobStatus.FAILED:
                        download.status = DownloadStatus.FAILED
                        download.error_message = (
                            job.error_message or "Download failed in Yubal"
                        )
                        updated_count += 1
                    elif job.status == YubalJobStatus.CANCELLED:
                        download.status = DownloadStatus.FAILED
                        download.error_message = "Download cancelled"
                        updated_count += 1
                    elif job.status.is_active:
                        download.status = DownloadStatus.DOWNLOADING

                session.commit()

                if updated_count > 0:
                    logger.info("Updated %d download(s) from Yubal", updated_count)

                # Try to submit pending downloads if queue has space
                await submit_pending_downloads(session, config)

                # Check for sync jobs that can now continue
                if updated_count > 0 and _sync_service:
                    await check_and_resume_sync_jobs(session)

            except Exception as e:
                logger.error("Error updating download statuses: %s", e)
            finally:
                session.close()

        except asyncio.CancelledError:
            logger.info("Download status update loop cancelled")
            break
        except Exception as e:
            logger.error("Unexpected error in download status update loop: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    import asyncio

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

    # Start background task to update download statuses
    update_task = asyncio.create_task(update_download_statuses_loop(config))

    logger.info("Jamknife started")
    yield

    # Cancel background task
    update_task.cancel()
    try:
        await update_task
    except asyncio.CancelledError:
        pass

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


@app.post("/api/sync-jobs/{job_id}/cancel")
async def cancel_sync_job(job_id: int, session: SessionDep):
    """Cancel a running sync job."""
    job = session.query(PlaylistSyncJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")

    if job.status in [SyncStatus.COMPLETED, SyncStatus.FAILED]:
        raise HTTPException(
            status_code=400, detail="Cannot cancel completed or failed job"
        )

    job.status = SyncStatus.FAILED
    job.error_message = "Cancelled by user"
    job.completed_at = datetime.now(timezone.utc)
    session.commit()

    return {"message": "Sync job cancelled", "job_id": job_id}


@app.post("/api/sync-jobs/{job_id}/complete")
async def force_complete_sync_job(
    job_id: int, session: SessionDep, sync_service: SyncServiceDep
):
    """Force complete a stuck sync job by creating playlist with available tracks."""
    job = session.query(PlaylistSyncJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")

    if job.status in [SyncStatus.COMPLETED, SyncStatus.FAILED]:
        raise HTTPException(status_code=400, detail="Job already completed or failed")

    try:
        # If stuck in DOWNLOADING, try to resume
        if job.status == SyncStatus.DOWNLOADING:
            sync_service.resume_sync_job_after_downloads(job_id)
        else:
            # For other states, mark as failed
            job.status = SyncStatus.FAILED
            job.error_message = "Force completed by user"
            job.completed_at = datetime.now(timezone.utc)
            session.commit()

        return {"message": "Sync job completed", "job_id": job_id}
    except Exception as e:
        logger.exception("Failed to force complete job %d", job_id)
        raise HTTPException(
            status_code=500, detail=f"Failed to complete job: {str(e)}"
        ) from e


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


@app.get("/api/downloads/active")
async def list_active_downloads(
    session: SessionDep, limit: int = 100
) -> list[AlbumDownloadResponse]:
    """List active album downloads (pending, queued, downloading)."""
    downloads = (
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
        .order_by(AlbumDownload.created_at.desc())
        .limit(limit)
        .all()
    )

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


@app.post("/api/downloads/{download_id}/retry")
async def retry_download(
    download_id: int, session: SessionDep, background_tasks: BackgroundTasks
):
    """Retry a failed download."""
    download = session.query(AlbumDownload).filter_by(id=download_id).first()
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")

    if download.status != DownloadStatus.FAILED:
        raise HTTPException(
            status_code=400, detail="Only failed downloads can be retried"
        )

    # Reset the download to pending
    download.status = DownloadStatus.PENDING
    download.progress = 0
    download.error_message = None
    download.completed_at = None
    session.commit()

    # Queue the download in the background
    config = get_config()
    yubal = YubalClient(config.yubal_url)

    def start_download():
        try:
            job = yubal.create_job(url=download.ytmusic_album_url)
            # Update status to queued
            s = _session_factory()
            try:
                d = s.query(AlbumDownload).filter_by(id=download_id).first()
                if d:
                    d.status = DownloadStatus.QUEUED
                    d.yubal_job_id = job.id
                    d.queued_at = datetime.now(timezone.utc)
                    s.commit()
            finally:
                s.close()
        except Exception as e:
            logger.exception("Failed to retry download %d", download_id)

            # Handle 409 Conflict - check for existing job
            if "409" in str(e) or "Conflict" in str(e):
                try:
                    all_jobs = yubal.list_jobs()
                    existing_job = next(
                        (j for j in all_jobs if j.url == download.ytmusic_album_url),
                        None,
                    )
                    if existing_job:
                        logger.info(
                            "Found existing job %s for download %d, linking",
                            existing_job.id,
                            download_id,
                        )
                        s = _session_factory()
                        try:
                            d = s.query(AlbumDownload).filter_by(id=download_id).first()
                            if d:
                                d.status = DownloadStatus.QUEUED
                                d.yubal_job_id = existing_job.id
                                d.queued_at = datetime.now(timezone.utc)
                                s.commit()
                        finally:
                            s.close()
                        return
                except Exception as list_err:
                    logger.warning("Failed to check for existing jobs: %s", list_err)

            # Failed - update download status
            s = _session_factory()
            try:
                d = s.query(AlbumDownload).filter_by(id=download_id).first()
                if d:
                    d.status = DownloadStatus.FAILED
                    if "409" in str(e) or "Conflict" in str(e):
                        d.error_message = "Yubal queue full - try again later"
                    else:
                        d.error_message = str(e)
                    s.commit()
            finally:
                s.close()

    background_tasks.add_task(start_download)

    return {"message": "Download retry initiated", "download_id": download_id}


@app.delete("/api/downloads/orphaned")
async def delete_orphaned_downloads(session: SessionDep):
    """Delete downloads that are not attached to any playlist."""
    # Find downloads with no associated track matches
    orphaned_downloads = (
        session.query(AlbumDownload)
        .outerjoin(TrackMatch, TrackMatch.album_download_id == AlbumDownload.id)
        .filter(TrackMatch.id.is_(None))
        .all()
    )

    count = len(orphaned_downloads)
    for download in orphaned_downloads:
        session.delete(download)

    session.commit()

    return {"message": f"Deleted {count} orphaned download(s)", "count": count}


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

    # Serialize active downloads for JSON in template
    active_downloads_json = [
        {
            "id": d.id,
            "album_name": d.album_name,
            "artist_name": d.artist_name,
            "status": d.status.value,
            "progress": d.progress,
        }
        for d in active_downloads
    ]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "config": config,
            "playlists": playlists,
            "recent_jobs": recent_jobs,
            "active_downloads": active_downloads,
            "active_downloads_json": active_downloads_json,
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
async def downloads_page(
    request: Request, session: SessionDep, status: str | None = None
):
    """Render the downloads page."""
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    config = get_config()

    query = session.query(AlbumDownload)
    if status:
        try:
            download_status = DownloadStatus(status)
            query = query.filter_by(status=download_status)
        except ValueError:
            pass  # Ignore invalid status, show all

    downloads = query.order_by(AlbumDownload.created_at.desc()).limit(100).all()

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

    # JSON-safe payload for template scripts
    job_json = {
        "id": job.id,
        "playlist_id": job.playlist_id,
        "playlist": {
            "id": job.playlist.id if job.playlist else job.playlist_id,
            "name": job.playlist.name if job.playlist else "Unknown Playlist",
        },
        "status": job.status.value,
        "error_message": job.error_message,
        "tracks_total": job.tracks_total,
        "tracks_matched": job.tracks_matched,
        "tracks_missing": job.tracks_missing,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }

    job_timestamps = {
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S")
        if job.created_at
        else "-",
        "started_at": job.started_at.strftime("%Y-%m-%d %H:%M:%S")
        if job.started_at
        else "-",
        "completed_at": job.completed_at.strftime("%Y-%m-%d %H:%M:%S")
        if job.completed_at
        else "-",
    }

    return templates.TemplateResponse(
        "sync_job_detail.html",
        {
            "request": request,
            "config": config,
            "job": job,
            "job_json": job_json,
            "job_timestamps": job_timestamps,
        },
    )
