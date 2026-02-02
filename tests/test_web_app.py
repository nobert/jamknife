"""Integration-style tests for web app routes and templates."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jamknife.database import (
    AlbumDownload,
    DownloadStatus,
    ListenBrainzPlaylist,
    PlaylistSyncJob,
    SyncStatus,
)
from jamknife.web.app import setup_templates

web_app = importlib.import_module("jamknife.web.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a TestClient with configured templates and temp database."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LISTENBRAINZ_USERNAME", "testuser")
    monkeypatch.setenv("PLEX_TOKEN", "testtoken")
    monkeypatch.setenv("YUBAL_URL", "http://yubal:8000")
    monkeypatch.setenv("PLEX_URL", "http://localhost:32400")

    templates_dir = Path(__file__).resolve().parents[1] / "src/jamknife/web/templates"
    setup_templates(str(templates_dir))

    # Reset globals to force lifespan init
    web_app._session_factory = None
    web_app._sync_service = None

    with TestClient(web_app.app) as test_client:
        yield test_client


def _create_playlist_and_job():
    """Create a playlist and sync job in the app database."""
    with web_app._session_factory() as session:
        playlist = ListenBrainzPlaylist(
            mbid="test-mbid-123",
            name="Daily Jams",
            creator="listenbrainz",
            is_daily=True,
            is_weekly=False,
        )
        session.add(playlist)
        session.commit()

        job = PlaylistSyncJob(
            playlist_id=playlist.id,
            status=SyncStatus.PENDING,
            tracks_total=10,
            tracks_matched=7,
            tracks_missing=3,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        return playlist.id, job.id


def _create_download():
    """Create a download record in the app database."""
    with web_app._session_factory() as session:
        download = AlbumDownload(
            ytmusic_album_id="album-123",
            ytmusic_album_url="https://music.youtube.com/playlist?list=OLAK5uy_test",
            album_name="Test Album",
            artist_name="Test Artist",
            status=DownloadStatus.QUEUED,
            progress=25,
        )
        session.add(download)
        session.commit()
        session.refresh(download)
        return download.id


def test_sync_job_detail_page_renders(client):
    """Ensure sync job detail page renders without template errors."""
    playlist_id, job_id = _create_playlist_and_job()

    response = client.get(f"/sync-jobs/{job_id}")

    assert response.status_code == 200
    assert f"/playlists/{playlist_id}" in response.text
    assert "Daily Jams" in response.text


def test_playlist_detail_page_renders(client):
    """Ensure playlist detail page renders without template errors."""
    playlist_id, _job_id = _create_playlist_and_job()

    response = client.get(f"/playlists/{playlist_id}")

    assert response.status_code == 200
    assert "Daily Jams" in response.text


def test_list_playlists_api_returns_json(client):
    """Ensure /api/playlists returns JSON with ORM attributes."""
    _playlist_id, _job_id = _create_playlist_and_job()

    response = client.get("/api/playlists")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert payload
    assert payload[0]["name"] == "Daily Jams"
    assert "enabled" in payload[0]
    assert "sync_day" in payload[0]
    assert "sync_time" in payload[0]


def test_index_page_renders(client):
    """Ensure index page renders with jobs and downloads."""
    _playlist_id, _job_id = _create_playlist_and_job()
    _download_id = _create_download()

    response = client.get("/")

    assert response.status_code == 200
    assert "Daily Jams" in response.text
    assert "Test Album" in response.text


def test_downloads_page_renders(client):
    """Ensure downloads page renders without template errors."""
    _download_id = _create_download()

    response = client.get("/downloads")

    assert response.status_code == 200
    assert "Test Album" in response.text


def test_downloads_page_filter_by_status(client):
    """Ensure downloads page status filter works."""
    with web_app._session_factory() as session:
        # Create a failed download
        failed_download = AlbumDownload(
            ytmusic_album_id="failed123",
            ytmusic_album_url="https://music.youtube.com/browse/failed123",
            album_name="Failed Album",
            artist_name="Failed Artist",
            status=DownloadStatus.FAILED,
            error_message="Test error message",
            progress=0,
        )
        session.add(failed_download)
        
        # Create a completed download
        completed_download = AlbumDownload(
            ytmusic_album_id="completed123",
            ytmusic_album_url="https://music.youtube.com/browse/completed123",
            album_name="Completed Album",
            artist_name="Completed Artist",
            status=DownloadStatus.COMPLETED,
            progress=100,
        )
        session.add(completed_download)
        session.commit()

    # Test filtering by failed status
    response = client.get("/downloads?status=failed")
    assert response.status_code == 200
    assert "Failed Album" in response.text
    assert "Test error message" in response.text
    assert "Completed Album" not in response.text

    # Test filtering by completed status
    response = client.get("/downloads?status=completed")
    assert response.status_code == 200
    assert "Completed Album" in response.text
    assert "Failed Album" not in response.text


def test_retry_download_endpoint(client, monkeypatch):
    """Ensure retry endpoint resets failed downloads."""
    # Mock YubalClient to avoid actual API calls
    from unittest.mock import Mock
    from jamknife.clients.yubal import YubalClient
    
    mock_job = Mock(id="job123")
    monkeypatch.setattr(YubalClient, "create_job", lambda self, url: mock_job)
    
    with web_app._session_factory() as session:
        download = AlbumDownload(
            ytmusic_album_id="retry123",
            ytmusic_album_url="https://music.youtube.com/browse/retry123",
            album_name="Retry Album",
            artist_name="Retry Artist",
            status=DownloadStatus.FAILED,
            error_message="Previous error",
            progress=50,
        )
        session.add(download)
        session.commit()
        download_id = download.id

    # Retry the download
    response = client.post(f"/api/downloads/{download_id}/retry")
    assert response.status_code == 200
    assert response.json()["download_id"] == download_id

    # Verify the download was reset (before background task completes)
    with web_app._session_factory() as session:
        download = session.query(AlbumDownload).filter_by(id=download_id).first()
        # Should be reset to pending immediately, background task will update to queued
        assert download.status in (DownloadStatus.PENDING, DownloadStatus.QUEUED)
        assert download.progress == 0
        assert download.error_message is None


def test_retry_non_failed_download_fails(client):
    """Ensure retry endpoint rejects non-failed downloads."""
    _download_id = _create_download()  # Creates a completed download

    response = client.post(f"/api/downloads/{_download_id}/retry")
    assert response.status_code == 400
    assert "Only failed downloads" in response.json()["detail"]
