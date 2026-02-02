"""Tests for database models."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jamknife.database import (
    AlbumDownload,
    Base,
    DownloadStatus,
    ListenBrainzPlaylist,
    MBIDPlexMapping,
    PlaylistSyncJob,
    SyncStatus,
    TrackMatch,
)


@pytest.fixture
def db_session():
    """Create a test database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_create_playlist(db_session):
    """Test creating a ListenBrainz playlist."""
    playlist = ListenBrainzPlaylist(
        mbid="test-mbid-123",
        name="Test Playlist",
        creator="Test Creator",
        is_daily=True,
        is_weekly=False,
    )
    db_session.add(playlist)
    db_session.commit()

    retrieved = db_session.query(ListenBrainzPlaylist).first()
    assert retrieved.mbid == "test-mbid-123"
    assert retrieved.name == "Test Playlist"
    assert retrieved.is_daily is True
    assert retrieved.created_at is not None


def test_create_sync_job(db_session):
    """Test creating a sync job."""
    playlist = ListenBrainzPlaylist(
        mbid="test-mbid",
        name="Test Playlist",
        creator="Creator",
    )
    db_session.add(playlist)
    db_session.commit()

    job = PlaylistSyncJob(playlist_id=playlist.id)
    db_session.add(job)
    db_session.commit()

    assert job.status == SyncStatus.PENDING
    assert job.tracks_total == 0
    assert job.tracks_matched == 0
    assert job.tracks_missing == 0


def test_create_track_match(db_session):
    """Test creating a track match."""
    playlist = ListenBrainzPlaylist(
        mbid="test-mbid",
        name="Test",
        creator="Creator",
    )
    db_session.add(playlist)
    db_session.commit()

    job = PlaylistSyncJob(playlist_id=playlist.id)
    db_session.add(job)
    db_session.commit()

    track = TrackMatch(
        sync_job_id=job.id,
        position=0,
        recording_mbid="track-mbid",
        track_name="Test Track",
        artist_name="Test Artist",
        matched_in_plex=False,
    )
    db_session.add(track)
    db_session.commit()

    assert track.track_name == "Test Track"
    assert track.matched_in_plex is False


def test_create_album_download(db_session):
    """Test creating an album download."""
    download = AlbumDownload(
        ytmusic_album_id="album-123",
        ytmusic_album_url="https://music.youtube.com/playlist?list=OLAK5uy_test",
        album_name="Test Album",
        artist_name="Test Artist",
    )
    db_session.add(download)
    db_session.commit()

    assert download.status == DownloadStatus.PENDING
    assert download.progress == 0


def test_create_mbid_mapping(db_session):
    """Test creating a MBID to Plex mapping."""
    mapping = MBIDPlexMapping(
        recording_mbid="test-mbid",
        plex_rating_key="12345",
        track_title="Test Track",
        artist_name="Test Artist",
    )
    db_session.add(mapping)
    db_session.commit()

    retrieved = db_session.query(MBIDPlexMapping).first()
    assert retrieved.recording_mbid == "test-mbid"
    assert retrieved.plex_rating_key == "12345"


def test_sync_status_enum():
    """Test SyncStatus enum values."""
    assert SyncStatus.PENDING.value == "pending"
    assert SyncStatus.FETCHING.value == "fetching"
    assert SyncStatus.MATCHING.value == "matching"
    assert SyncStatus.DOWNLOADING.value == "downloading"
    assert SyncStatus.CREATING_PLAYLIST.value == "creating_playlist"
    assert SyncStatus.COMPLETED.value == "completed"
    assert SyncStatus.FAILED.value == "failed"


def test_download_status_enum():
    """Test DownloadStatus enum values."""
    assert DownloadStatus.PENDING.value == "pending"
    assert DownloadStatus.QUEUED.value == "queued"
    assert DownloadStatus.DOWNLOADING.value == "downloading"
    assert DownloadStatus.COMPLETED.value == "completed"
    assert DownloadStatus.FAILED.value == "failed"
