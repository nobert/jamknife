"""Tests for client modules."""

from unittest.mock import Mock, patch

import httpx

from jamknife.clients.listenbrainz import ListenBrainzClient
from jamknife.clients.ytmusic import YTMusicResolver
from jamknife.clients.yubal import JobStatus, YubalClient


class TestListenBrainzClient:
    """Tests for ListenBrainz client."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = ListenBrainzClient(token="test-token")
        assert client._token == "test-token"

    def test_headers_with_token(self):
        """Test headers include token when provided."""
        client = ListenBrainzClient(token="test-token")
        headers = client._headers()
        assert headers["Authorization"] == "Token test-token"

    def test_headers_without_token(self):
        """Test headers without token."""
        client = ListenBrainzClient()
        headers = client._headers()
        assert "Authorization" not in headers

    @patch("jamknife.clients.listenbrainz.httpx.Client")
    def test_get_user_playlists(self, mock_client_class):
        """Test fetching user playlists."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "playlists": [{"identifier": "https://musicbrainz.org/playlist/test-mbid"}]
        }
        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = ListenBrainzClient()
        playlists = client.get_user_playlists("testuser")

        assert len(playlists) == 1

    def test_parse_track_with_list_identifier(self):
        """Test that identifier lists are handled correctly."""
        client = ListenBrainzClient()
        track = client._parse_track(
            {
                "identifier": [
                    "https://musicbrainz.org/recording/8a65705b-c08a-455b-910e-a69ed72c68f5"
                ],
                "title": "Test Track",
                "creator": "Test Artist",
            }
        )

        assert track is not None
        assert track.recording_mbid == "8a65705b-c08a-455b-910e-a69ed72c68f5"

    @patch("jamknife.clients.listenbrainz.httpx.Client")
    @patch("jamknife.clients.listenbrainz.time.sleep")
    def test_retry_logic_on_connection_error(self, mock_sleep, mock_client_class):
        """Test that the client retries on connection errors."""
        mock_client = Mock()
        # First two attempts fail, third succeeds
        mock_client.get.side_effect = [
            httpx.ConnectError("Connection reset by peer"),
            httpx.ConnectError("Connection reset by peer"),
            Mock(json=lambda: {"test": "data"}, raise_for_status=lambda: None),
        ]
        mock_client_class.return_value = mock_client

        client = ListenBrainzClient(max_retries=3)
        result = client._get("/test")

        assert result == {"test": "data"}
        assert mock_client.get.call_count == 3
        assert mock_sleep.call_count == 2  # Slept between retries

    @patch("jamknife.clients.listenbrainz.httpx.Client")
    @patch("jamknife.clients.listenbrainz.time.sleep")
    def test_all_retries_fail(self, mock_sleep, mock_client_class):
        """Test that ConnectError is raised after all retries fail."""
        mock_client = Mock()
        mock_client.get.side_effect = httpx.ConnectError("Connection reset by peer")
        mock_client_class.return_value = mock_client

        client = ListenBrainzClient(max_retries=3)
        
        try:
            client._get("/test")
            assert False, "Expected ConnectError to be raised"
        except httpx.ConnectError as e:
            assert "after 3 attempts" in str(e)
            assert mock_client.get.call_count == 3


class TestYubalClient:
    """Tests for Yubal client."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = YubalClient("http://localhost:8000")
        assert client._base_url == "http://localhost:8000"

    def test_job_status_enum(self):
        """Test JobStatus enum."""
        assert JobStatus.PENDING.is_active
        assert JobStatus.DOWNLOADING.is_active
        assert JobStatus.COMPLETED.is_finished
        assert JobStatus.FAILED.is_finished
        assert not JobStatus.COMPLETED.is_active

    @patch("jamknife.clients.yubal.httpx.Client")
    def test_health_check_success(self, mock_client_class):
        """Test successful health check."""
        mock_response = Mock()
        mock_response.json.return_value = {"status": "healthy"}
        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = YubalClient("http://localhost:8000")
        assert client.health_check() is True

    @patch("jamknife.clients.yubal.httpx.Client")
    def test_health_check_failure(self, mock_client_class):
        """Test failed health check."""
        mock_client = Mock()
        mock_client.get.side_effect = Exception("Connection error")
        mock_client_class.return_value = mock_client

        client = YubalClient("http://localhost:8000")
        assert client.health_check() is False


class TestYTMusicResolver:
    """Tests for YouTube Music resolver."""

    def test_resolver_initialization(self):
        """Test resolver initialization."""
        with patch("jamknife.clients.ytmusic.YTMusic"):
            resolver = YTMusicResolver()
            assert resolver._ytm is not None

    def test_album_url_template(self):
        """Test album URL template."""
        assert "music.youtube.com" in YTMusicResolver.ALBUM_URL_TEMPLATE

    @patch("jamknife.clients.ytmusic.YTMusic")
    def test_names_match(self, mock_ytmusic):
        """Test name matching logic."""
        resolver = YTMusicResolver()

        # Exact match
        assert resolver._names_match("Test Song", "Test Song")

        # Case insensitive
        assert resolver._names_match("Test Song", "test song")

        # Different names
        assert not resolver._names_match("Song A", "Song B")

    @patch("jamknife.clients.ytmusic.YTMusic")
    def test_find_album_returns_none_on_no_match(self, mock_ytmusic):
        """Test that find_album_for_track returns None when no match."""
        mock_ytmusic.return_value.search.return_value = []

        resolver = YTMusicResolver()
        result = resolver.find_album_for_track("Test Track", "Test Artist")

        assert result is None
