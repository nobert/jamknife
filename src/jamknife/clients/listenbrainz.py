"""ListenBrainz API client for fetching playlists."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LISTENBRAINZ_API_BASE = "https://api.listenbrainz.org/1"


@dataclass
class Track:
    """A track from a ListenBrainz playlist."""

    recording_mbid: str
    title: str
    artist: str
    album: str | None = None
    release_mbid: str | None = None
    artist_mbids: list[str] | None = None


@dataclass
class Playlist:
    """A ListenBrainz playlist with metadata and tracks."""

    mbid: str
    name: str
    creator: str
    created_for: str | None
    date: str | None
    tracks: list[Track]


class ListenBrainzClient:
    """Client for the ListenBrainz API."""

    def __init__(
        self, token: str | None = None, timeout: float = 30.0, max_retries: int = 3
    ):
        """Initialize the client.

        Args:
            token: Optional ListenBrainz user token for authenticated requests.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retry attempts for failed requests.
        """
        self._token = token
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Token {self._token}"
        return headers

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict:
        """Make a GET request to the API with retry logic."""
        url = f"{LISTENBRAINZ_API_BASE}{endpoint}"

        last_error = None
        for attempt in range(self._max_retries):
            try:
                logger.debug(
                    f"ListenBrainz API request (attempt {attempt + 1}/{self._max_retries}): GET {url}"
                )
                response = self._client.get(url, headers=self._headers(), params=params)
                response.raise_for_status()
                return response.json()
            except httpx.ConnectError as e:
                last_error = e
                logger.warning(
                    f"Connection error on attempt {attempt + 1}/{self._max_retries} "
                    f"for {url}: {e}"
                )
                if attempt < self._max_retries - 1:
                    delay = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    f"Timeout on attempt {attempt + 1}/{self._max_retries} "
                    f"for {url}: {e}"
                )
                if attempt < self._max_retries - 1:
                    delay = 2**attempt
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)
            except httpx.HTTPStatusError as e:
                # Don't retry on HTTP errors (4xx, 5xx)
                logger.error(
                    f"HTTP error for {url}: {e.response.status_code} {e.response.text}"
                )
                raise

        # All retries exhausted
        logger.error(
            f"All {self._max_retries} retry attempts failed for {url}. "
            f"Last error: {last_error}"
        )
        raise httpx.ConnectError(
            f"Failed to connect to ListenBrainz API after {self._max_retries} attempts. "
            f"This may be a network issue, firewall blocking the connection, or a problem "
            f"with the ListenBrainz API. Last error: {last_error}"
        ) from last_error

    def get_user_playlists(
        self, username: str, count: int = 25, offset: int = 0
    ) -> list[dict]:
        """Fetch user's own playlists (metadata only, no tracks).

        Args:
            username: ListenBrainz username.
            count: Number of playlists to return.
            offset: Pagination offset.

        Returns:
            List of playlist metadata dictionaries.
        """
        data = self._get(
            f"/user/{username}/playlists",
            params={"count": count, "offset": offset},
        )
        return data.get("playlists", [])

    def get_playlists_created_for(
        self, username: str, count: int = 25, offset: int = 0
    ) -> list[dict]:
        """Fetch playlists created for a user (daily/weekly jams, recommendations).

        Args:
            username: ListenBrainz username.
            count: Number of playlists to return.
            offset: Pagination offset.

        Returns:
            List of playlist metadata dictionaries.
        """
        data = self._get(
            f"/user/{username}/playlists/createdfor",
            params={"count": count, "offset": offset},
        )
        return data.get("playlists", [])

    def get_playlist(self, playlist_mbid: str, fetch_metadata: bool = True) -> Playlist:
        """Fetch a full playlist with tracks.

        Args:
            playlist_mbid: The playlist's MBID.
            fetch_metadata: Whether to fetch recording metadata.

        Returns:
            Playlist object with tracks.
        """
        params = {} if fetch_metadata else {"fetch_metadata": "false"}
        data = self._get(f"/playlist/{playlist_mbid}", params=params)

        playlist_data = data.get("playlist", {})
        extension = playlist_data.get("extension", {})
        lb_ext = extension.get("https://musicbrainz.org/doc/jspf#playlist", {})

        tracks = []
        for track_data in playlist_data.get("track", []):
            track = self._parse_track(track_data)
            if track:
                tracks.append(track)

        # Extract MBID from identifier URL
        identifier = playlist_data.get("identifier", "")
        mbid = identifier.split("/")[-1] if identifier else playlist_mbid

        return Playlist(
            mbid=mbid,
            name=playlist_data.get("title", "Unknown Playlist"),
            creator=lb_ext.get("creator", playlist_data.get("creator", "")),
            created_for=lb_ext.get("created_for"),
            date=playlist_data.get("date"),
            tracks=tracks,
        )

    def _parse_track(self, track_data: dict) -> Track | None:
        """Parse a track from JSPF format."""
        identifier = track_data.get("identifier", "")
        if not identifier:
            return None

        # Handle identifier as list or string (API inconsistency)
        if isinstance(identifier, list):
            identifier = identifier[0] if identifier else ""

        if not identifier:
            return None

        # Extract MBID from identifier URL
        # Format: https://musicbrainz.org/recording/<mbid>
        recording_mbid = identifier.split("/")[-1] if "/" in identifier else identifier

        extension = track_data.get("extension", {})
        track_ext = extension.get("https://musicbrainz.org/doc/jspf#track", {})

        # Extract release MBID if present
        release_identifier = track_ext.get("release_identifier", "")
        release_mbid = None
        if release_identifier:
            release_mbid = release_identifier.split("/")[-1]

        # Extract artist MBIDs
        artist_identifiers = track_ext.get("artist_identifiers", [])
        artist_mbids = [url.split("/")[-1] for url in artist_identifiers if "/" in url]

        return Track(
            recording_mbid=recording_mbid,
            title=track_data.get("title", "Unknown Track"),
            artist=track_data.get("creator", "Unknown Artist"),
            album=track_data.get("album"),
            release_mbid=release_mbid,
            artist_mbids=artist_mbids if artist_mbids else None,
        )

    def is_daily_jams_playlist(self, playlist_data: dict) -> bool:
        """Check if a playlist is a Daily Jams playlist."""
        playlist = playlist_data.get("playlist", playlist_data)
        title = playlist.get("title", "")
        return "Daily Jams" in title or "daily-jams" in title.lower()

    def is_weekly_jams_playlist(self, playlist_data: dict) -> bool:
        """Check if a playlist is a Weekly Jams playlist."""
        playlist = playlist_data.get("playlist", playlist_data)
        title = playlist.get("title", "")
        return "Weekly Jams" in title or "weekly-jams" in title.lower()

    def is_weekly_exploration_playlist(self, playlist_data: dict) -> bool:
        """Check if a playlist is a Weekly Exploration playlist."""
        playlist = playlist_data.get("playlist", playlist_data)
        title = playlist.get("title", "")
        return "Weekly Exploration" in title or "weekly-exploration" in title.lower()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "ListenBrainzClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
