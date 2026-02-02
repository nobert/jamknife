"""Playlist sync orchestration service."""

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from jamknife.clients import (
    ListenBrainzClient,
    PlexClient,
    YTMusicResolver,
    YubalClient,
)
from jamknife.clients.yubal import JobStatus as YubalJobStatus
from jamknife.config import Config
from jamknife.database import (
    AlbumDownload,
    DownloadStatus,
    ListenBrainzPlaylist,
    MBIDPlexMapping,
    PlaylistSyncJob,
    SyncStatus,
    TrackMatch,
)

logger = logging.getLogger(__name__)

# Type alias for progress callback
ProgressCallback = Callable[[str, float], None]


class PlaylistSyncService:
    """Service for syncing ListenBrainz playlists to Plex."""

    def __init__(
        self,
        config: Config,
        session_factory: Callable[[], Session],
    ):
        """Initialize the sync service.

        Args:
            config: Application configuration.
            session_factory: SQLAlchemy session factory.
        """
        self._config = config
        self._session_factory = session_factory

    def discover_playlists(self) -> list[ListenBrainzPlaylist]:
        """Discover daily/weekly playlists from ListenBrainz.

        Returns:
            List of discovered playlists (not yet in database).
        """
        discovered = []

        with ListenBrainzClient(self._config.listenbrainz_token) as lb:
            # Fetch playlists created for the user (daily/weekly jams)
            playlists_data = lb.get_playlists_created_for(
                self._config.listenbrainz_username, count=50
            )

            with self._session_factory() as session:
                for pl_data in playlists_data:
                    playlist_info = pl_data.get("playlist", pl_data)
                    identifier = playlist_info.get("identifier", "")
                    mbid = identifier.split("/")[-1] if "/" in identifier else identifier

                    if not mbid:
                        continue

                    # Check if already tracked
                    existing = (
                        session.query(ListenBrainzPlaylist)
                        .filter_by(mbid=mbid)
                        .first()
                    )
                    if existing:
                        continue

                    # Determine playlist type
                    is_daily = lb.is_daily_jams_playlist(pl_data)
                    is_weekly = (
                        lb.is_weekly_jams_playlist(pl_data)
                        or lb.is_weekly_exploration_playlist(pl_data)
                    )

                    if not (is_daily or is_weekly):
                        continue

                    extension = playlist_info.get("extension", {})
                    lb_ext = extension.get(
                        "https://musicbrainz.org/doc/jspf#playlist", {}
                    )

                    playlist = ListenBrainzPlaylist(
                        mbid=mbid,
                        name=playlist_info.get("title", "Unknown Playlist"),
                        creator=lb_ext.get(
                            "creator", playlist_info.get("creator", "")
                        ),
                        created_for=lb_ext.get("created_for"),
                        is_daily=is_daily,
                        is_weekly=is_weekly,
                    )
                    discovered.append(playlist)

        return discovered

    def add_playlist(self, playlist: ListenBrainzPlaylist) -> ListenBrainzPlaylist:
        """Add a playlist to tracking.

        Args:
            playlist: The playlist to add.

        Returns:
            The persisted playlist.
        """
        with self._session_factory() as session:
            session.add(playlist)
            session.commit()
            session.refresh(playlist)
            return playlist

    def create_sync_job(self, playlist_id: int) -> PlaylistSyncJob:
        """Create a new sync job for a playlist.

        Args:
            playlist_id: ID of the playlist to sync.

        Returns:
            The created sync job.
        """
        with self._session_factory() as session:
            job = PlaylistSyncJob(playlist_id=playlist_id)
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def run_sync_job(
        self,
        job_id: int,
        on_progress: ProgressCallback | None = None,
    ) -> PlaylistSyncJob:
        """Execute a playlist sync job.

        This is the main orchestration method that:
        1. Fetches the playlist from ListenBrainz
        2. Matches tracks against Plex library
        3. Resolves missing tracks to YouTube Music albums
        4. Submits albums for download via Yubal
        5. Waits for downloads to complete
        6. Creates the playlist in Plex

        Args:
            job_id: ID of the sync job to run.
            on_progress: Optional callback for progress updates.

        Returns:
            The completed sync job.
        """
        with self._session_factory() as session:
            job = session.query(PlaylistSyncJob).get(job_id)
            if not job:
                raise ValueError(f"Sync job not found: {job_id}")

            playlist = job.playlist

            try:
                # Phase 1: Fetch playlist tracks
                self._update_job_status(session, job, SyncStatus.FETCHING)
                if on_progress:
                    on_progress("Fetching playlist from ListenBrainz", 0.05)

                with ListenBrainzClient(self._config.listenbrainz_token) as lb:
                    lb_playlist = lb.get_playlist(playlist.mbid)

                job.tracks_total = len(lb_playlist.tracks)
                session.commit()

                # Phase 2: Match tracks against Plex
                self._update_job_status(session, job, SyncStatus.MATCHING)
                if on_progress:
                    on_progress("Matching tracks in Plex library", 0.10)

                plex = PlexClient(
                    self._config.plex_url,
                    self._config.plex_token,
                    self._config.plex_music_library,
                )
                ytmusic = YTMusicResolver()

                matched_tracks = []
                missing_tracks = []

                for i, track in enumerate(lb_playlist.tracks):
                    progress = 0.10 + (0.30 * (i / len(lb_playlist.tracks)))
                    if on_progress:
                        on_progress(
                            f"Matching track {i+1}/{len(lb_playlist.tracks)}", progress
                        )

                    track_match = self._match_track(
                        session, job, track, i, plex, ytmusic
                    )
                    session.add(track_match)

                    if track_match.matched_in_plex:
                        matched_tracks.append(track_match)
                    else:
                        missing_tracks.append(track_match)

                job.tracks_matched = len(matched_tracks)
                job.tracks_missing = len(missing_tracks)
                session.commit()

                # Phase 3: Download missing albums
                if missing_tracks:
                    self._update_job_status(session, job, SyncStatus.DOWNLOADING)
                    if on_progress:
                        on_progress("Downloading missing albums", 0.40)

                    self._download_missing_albums(
                        session, missing_tracks, on_progress, 0.40, 0.80
                    )

                    # Trigger Plex library refresh and wait
                    if on_progress:
                        on_progress("Refreshing Plex library", 0.82)
                    plex.refresh_library()

                    # Wait for library to update (simple delay for now)
                    import time
                    time.sleep(10)

                    # Re-match previously missing tracks
                    if on_progress:
                        on_progress("Re-matching downloaded tracks", 0.85)

                    for track_match in missing_tracks:
                        if track_match.album_download:
                            download = track_match.album_download
                            if download.status == DownloadStatus.COMPLETED:
                                plex_match = plex.search_track_by_album_and_title(
                                    download.album_name,
                                    track_match.track_name,
                                    download.artist_name,
                                )
                                if plex_match:
                                    track_match.plex_rating_key = plex_match.rating_key
                                    track_match.matched_in_plex = True
                                    job.tracks_matched += 1
                                    job.tracks_missing -= 1

                                    # Cache the mapping
                                    self._cache_mbid_mapping(
                                        session,
                                        track_match.recording_mbid,
                                        plex_match.rating_key,
                                        track_match.track_name,
                                        track_match.artist_name,
                                        track_match.album_name,
                                    )

                    session.commit()

                # Phase 4: Create Plex playlist
                self._update_job_status(session, job, SyncStatus.CREATING_PLAYLIST)
                if on_progress:
                    on_progress("Creating Plex playlist", 0.90)

                plex_tracks = []
                for track_match in job.track_matches:
                    if track_match.matched_in_plex and track_match.plex_rating_key:
                        plex_track = plex.get_track_by_rating_key(
                            track_match.plex_rating_key
                        )
                        if plex_track:
                            plex_tracks.append(plex_track)

                if plex_tracks:
                    plex_playlist = plex.create_playlist(
                        lb_playlist.name, plex_tracks, replace_existing=True
                    )
                    job.plex_playlist_key = str(plex_playlist.ratingKey)

                # Mark as completed
                self._update_job_status(session, job, SyncStatus.COMPLETED)
                job.completed_at = datetime.now(datetime.UTC)
                playlist.last_synced_at = datetime.now(datetime.UTC)
                session.commit()

                if on_progress:
                    on_progress("Sync completed", 1.0)

            except Exception as e:
                logger.exception("Sync job %d failed", job_id)
                job.status = SyncStatus.FAILED
                job.error_message = str(e)
                job.completed_at = datetime.now(datetime.UTC)
                session.commit()
                raise

            return job

    def _match_track(
        self,
        session: Session,
        job: PlaylistSyncJob,
        track,
        position: int,
        plex: PlexClient,
        ytmusic: YTMusicResolver,
    ) -> TrackMatch:
        """Match a single track against Plex and resolve to YouTube Music."""
        # Check cached MBID mapping first
        cached = (
            session.query(MBIDPlexMapping)
            .filter_by(recording_mbid=track.recording_mbid)
            .first()
        )

        if cached:
            # Verify the track still exists in Plex
            plex_track = plex.get_track_by_rating_key(cached.plex_rating_key)
            if plex_track:
                return TrackMatch(
                    sync_job_id=job.id,
                    position=position,
                    recording_mbid=track.recording_mbid,
                    track_name=track.title,
                    artist_name=track.artist,
                    album_name=track.album,
                    release_mbid=track.release_mbid,
                    plex_rating_key=cached.plex_rating_key,
                    matched_in_plex=True,
                )

        # Try to match in Plex
        plex_match = plex.search_track(track.title, track.artist, track.album)

        if plex_match:
            # Cache the mapping
            self._cache_mbid_mapping(
                session,
                track.recording_mbid,
                plex_match.rating_key,
                track.title,
                track.artist,
                track.album,
            )

            return TrackMatch(
                sync_job_id=job.id,
                position=position,
                recording_mbid=track.recording_mbid,
                track_name=track.title,
                artist_name=track.artist,
                album_name=track.album,
                release_mbid=track.release_mbid,
                plex_rating_key=plex_match.rating_key,
                matched_in_plex=True,
            )

        # Track not in Plex, resolve to YouTube Music album
        track_match = TrackMatch(
            sync_job_id=job.id,
            position=position,
            recording_mbid=track.recording_mbid,
            track_name=track.title,
            artist_name=track.artist,
            album_name=track.album,
            release_mbid=track.release_mbid,
            matched_in_plex=False,
        )

        album_info = ytmusic.find_album_for_track(track.title, track.artist, track.album)
        if album_info:
            track_match.ytmusic_album_id = album_info.album_id
            track_match.ytmusic_album_url = album_info.url

            # Check if album download already exists
            existing_download = (
                session.query(AlbumDownload)
                .filter_by(ytmusic_album_id=album_info.album_id)
                .first()
            )

            if existing_download:
                track_match.album_download_id = existing_download.id
            else:
                # Create new album download record
                download = AlbumDownload(
                    ytmusic_album_id=album_info.album_id,
                    ytmusic_album_url=album_info.url,
                    album_name=album_info.title,
                    artist_name=album_info.artist,
                )
                session.add(download)
                session.flush()
                track_match.album_download_id = download.id

        return track_match

    def _download_missing_albums(
        self,
        session: Session,
        missing_tracks: list[TrackMatch],
        on_progress: ProgressCallback | None,
        progress_start: float,
        progress_end: float,
    ) -> None:
        """Download albums for missing tracks via Yubal."""
        # Collect unique albums to download
        albums_to_download: dict[int, AlbumDownload] = {}
        for track in missing_tracks:
            if track.album_download_id:
                download = session.query(AlbumDownload).get(track.album_download_id)
                if download and download.status == DownloadStatus.PENDING:
                    albums_to_download[download.id] = download

        if not albums_to_download:
            return

        yubal = YubalClient(self._config.yubal_url)

        try:
            # Submit all albums for download
            for i, download in enumerate(albums_to_download.values()):
                try:
                    logger.info(
                        "Submitting album for download: %s - %s",
                        download.artist_name,
                        download.album_name,
                    )

                    job = yubal.create_job(download.ytmusic_album_url)
                    download.yubal_job_id = job.id
                    download.status = DownloadStatus.QUEUED
                    download.queued_at = datetime.now(timezone.utc)
                    session.commit()

                except Exception as e:
                    logger.error(
                        "Failed to submit album %s: %s", download.album_name, e
                    )
                    download.status = DownloadStatus.FAILED
                    download.error_message = str(e)
                    session.commit()

            # Wait for all downloads to complete
            pending_downloads = [
                d for d in albums_to_download.values() if d.yubal_job_id
            ]
            completed = 0
            total = len(pending_downloads)

            while pending_downloads:
                import time
                time.sleep(5)

                for download in pending_downloads[:]:
                    try:
                        job = yubal.get_job(download.yubal_job_id)
                        download.progress = job.progress

                        if job.status == YubalJobStatus.COMPLETED:
                            download.status = DownloadStatus.COMPLETED
                            download.completed_at = datetime.now(datetime.UTC)
                            pending_downloads.remove(download)
                            completed += 1
                        elif job.status == YubalJobStatus.FAILED:
                            download.status = DownloadStatus.FAILED
                            download.error_message = "Download failed in Yubal"
                            pending_downloads.remove(download)
                            completed += 1
                        elif job.status == YubalJobStatus.CANCELLED:
                            download.status = DownloadStatus.FAILED
                            download.error_message = "Download cancelled"
                            pending_downloads.remove(download)
                            completed += 1
                        elif job.status.is_active:
                            download.status = DownloadStatus.DOWNLOADING

                        session.commit()

                    except Exception as e:
                        logger.error(
                            "Failed to check download status for %s: %s",
                            download.album_name,
                            e,
                        )

                if on_progress and total > 0:
                    progress = progress_start + (
                        (progress_end - progress_start) * (completed / total)
                    )
                    on_progress(
                        f"Downloading albums ({completed}/{total} complete)", progress
                    )

        finally:
            yubal.close()

    def _cache_mbid_mapping(
        self,
        session: Session,
        recording_mbid: str,
        plex_rating_key: str,
        track_title: str,
        artist_name: str,
        album_name: str | None,
    ) -> None:
        """Cache a MBID to Plex rating key mapping."""
        existing = (
            session.query(MBIDPlexMapping)
            .filter_by(recording_mbid=recording_mbid)
            .first()
        )

        if existing:
            existing.plex_rating_key = plex_rating_key
            existing.track_title = track_title
            existing.artist_name = artist_name
            existing.album_name = album_name
        else:
            mapping = MBIDPlexMapping(
                recording_mbid=recording_mbid,
                plex_rating_key=plex_rating_key,
                track_title=track_title,
                artist_name=artist_name,
                album_name=album_name,
            )
            session.add(mapping)

    def _update_job_status(
        self, session: Session, job: PlaylistSyncJob, status: SyncStatus
    ) -> None:
        """Update job status and started_at timestamp."""
        job.status = status
        if status != SyncStatus.PENDING and not job.started_at:
            job.started_at = datetime.now(datetime.UTC)
        session.commit()

    def get_sync_jobs(
        self, playlist_id: int | None = None, limit: int = 50
    ) -> list[PlaylistSyncJob]:
        """Get sync jobs, optionally filtered by playlist.

        Args:
            playlist_id: Optional playlist ID to filter by.
            limit: Maximum number of jobs to return.

        Returns:
            List of sync jobs (newest first).
        """
        with self._session_factory() as session:
            query = session.query(PlaylistSyncJob)
            if playlist_id:
                query = query.filter_by(playlist_id=playlist_id)
            query = query.order_by(PlaylistSyncJob.created_at.desc()).limit(limit)
            return query.all()

    def get_playlists(self) -> list[ListenBrainzPlaylist]:
        """Get all tracked playlists.

        Returns:
            List of tracked playlists.
        """
        with self._session_factory() as session:
            return (
                session.query(ListenBrainzPlaylist)
                .order_by(ListenBrainzPlaylist.created_at.desc())
                .all()
            )

    def get_album_downloads(
        self, status: DownloadStatus | None = None, limit: int = 100
    ) -> list[AlbumDownload]:
        """Get album downloads, optionally filtered by status.

        Args:
            status: Optional status to filter by.
            limit: Maximum number of downloads to return.

        Returns:
            List of album downloads (newest first).
        """
        with self._session_factory() as session:
            query = session.query(AlbumDownload)
            if status:
                query = query.filter_by(status=status)
            query = query.order_by(AlbumDownload.created_at.desc()).limit(limit)
            return query.all()
