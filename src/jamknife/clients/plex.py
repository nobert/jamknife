"""Plex client for library search and playlist management."""

import logging
from dataclasses import dataclass

from plexapi.audio import Album, Artist, Track
from plexapi.exceptions import NotFound
from plexapi.playlist import Playlist
from plexapi.server import PlexServer

logger = logging.getLogger(__name__)


@dataclass
class PlexTrackMatch:
    """Result of matching a track in Plex."""

    rating_key: str
    title: str
    artist: str
    album: str | None
    track: Track


class PlexClient:
    """Client for Plex Media Server operations."""

    def __init__(self, base_url: str, token: str, music_library: str = "Music"):
        """Initialize the Plex client.

        Args:
            base_url: Plex server URL (e.g., http://localhost:32400).
            token: Plex authentication token.
            music_library: Name of the music library section.
        """
        self._base_url = base_url
        self._token = token
        self._music_library_name = music_library
        self._server: PlexServer | None = None
        self._music_section = None

    def _connect(self) -> PlexServer:
        """Establish connection to Plex server."""
        if self._server is None:
            self._server = PlexServer(self._base_url, self._token)
            logger.info("Connected to Plex server: %s", self._server.friendlyName)
        return self._server

    def _get_music_section(self):
        """Get the music library section."""
        if self._music_section is None:
            server = self._connect()
            self._music_section = server.library.section(self._music_library_name)
        return self._music_section

    def search_track(
        self, title: str, artist: str, album: str | None = None
    ) -> PlexTrackMatch | None:
        """Search for a track in the Plex library.

        Uses a multi-stage search strategy:
        1. Exact match on title + artist + album (if provided)
        2. Exact match on title + artist
        3. Fuzzy search with ranking

        Args:
            title: Track title.
            artist: Artist name.
            album: Optional album name for more precise matching.

        Returns:
            PlexTrackMatch if found, None otherwise.
        """
        music = self._get_music_section()

        # Strategy 1: Search by title and filter by artist
        try:
            results = music.searchTracks(title=title)
            for track in results:
                track_artist = self._get_track_artist(track)
                if self._names_match(track_artist, artist):
                    if album and track.parentTitle:
                        if self._names_match(track.parentTitle, album):
                            return self._create_match(track)
                    else:
                        return self._create_match(track)
        except Exception as e:
            logger.debug("Track search failed: %s", e)

        # Strategy 2: Search by artist first, then find track
        try:
            artist_results = music.searchArtists(title=artist)
            for plex_artist in artist_results:
                if self._names_match(plex_artist.title, artist):
                    for plex_album in plex_artist.albums():
                        for track in plex_album.tracks():
                            if self._names_match(track.title, title):
                                if album:
                                    if self._names_match(plex_album.title, album):
                                        return self._create_match(track)
                                else:
                                    return self._create_match(track)
        except Exception as e:
            logger.debug("Artist-based search failed: %s", e)

        # Strategy 3: Broad search
        try:
            results = music.search(f"{artist} {title}", mediatype="track", limit=20)
            for track in results:
                track_artist = self._get_track_artist(track)
                if self._names_match(track.title, title) and self._names_match(
                    track_artist, artist
                ):
                    return self._create_match(track)
        except Exception as e:
            logger.debug("Broad search failed: %s", e)

        return None

    def search_track_by_album_and_title(
        self, album_name: str, track_title: str, artist_name: str | None = None
    ) -> PlexTrackMatch | None:
        """Search for a track by album name and track title.

        Useful after downloading an album to find the specific track.

        Args:
            album_name: Name of the album.
            track_title: Title of the track.
            artist_name: Optional artist name for disambiguation.

        Returns:
            PlexTrackMatch if found, None otherwise.
        """
        music = self._get_music_section()

        try:
            albums = music.searchAlbums(title=album_name)
            for album in albums:
                if artist_name:
                    album_artist = album.parentTitle or ""
                    if not self._names_match(album_artist, artist_name):
                        continue

                for track in album.tracks():
                    if self._names_match(track.title, track_title):
                        return self._create_match(track)
        except Exception as e:
            logger.debug("Album-based track search failed: %s", e)

        return None

    def get_track_by_rating_key(self, rating_key: str) -> Track | None:
        """Get a track by its Plex rating key."""
        try:
            server = self._connect()
            return server.fetchItem(int(rating_key))
        except (NotFound, ValueError):
            return None

    def create_playlist(
        self, name: str, tracks: list[Track], replace_existing: bool = True
    ) -> Playlist:
        """Create or update a playlist with the given tracks.

        Args:
            name: Name of the playlist.
            tracks: List of Plex Track objects to add.
            replace_existing: If True, delete existing playlist with same name.

        Returns:
            The created Playlist object.
        """
        server = self._connect()

        if replace_existing:
            try:
                existing = server.playlist(name)
                logger.info("Deleting existing playlist: %s", name)
                existing.delete()
            except NotFound:
                pass

        playlist = server.createPlaylist(name, items=tracks)
        logger.info("Created playlist '%s' with %d tracks", name, len(tracks))
        return playlist

    def refresh_library(self) -> None:
        """Trigger a library refresh for the music section."""
        music = self._get_music_section()
        music.refresh()
        logger.info("Triggered library refresh for %s", self._music_library_name)

    def _get_track_artist(self, track: Track) -> str:
        """Get the artist name for a track."""
        if track.originalTitle:
            return track.originalTitle
        if track.grandparentTitle:
            return track.grandparentTitle
        return ""

    def _names_match(self, name1: str, name2: str) -> bool:
        """Check if two names match (case-insensitive, normalized)."""
        if not name1 or not name2:
            return False
        n1 = self._normalize_name(name1)
        n2 = self._normalize_name(name2)
        return n1 == n2 or n1 in n2 or n2 in n1

    def _normalize_name(self, name: str) -> str:
        """Normalize a name for comparison."""
        # Lowercase, remove common variations
        name = name.lower().strip()
        # Remove "the " prefix
        if name.startswith("the "):
            name = name[4:]
        # Remove common punctuation
        for char in ["'", '"', ".", ",", "!", "?", "(", ")", "[", "]"]:
            name = name.replace(char, "")
        return name

    def _create_match(self, track: Track) -> PlexTrackMatch:
        """Create a PlexTrackMatch from a Plex Track."""
        return PlexTrackMatch(
            rating_key=str(track.ratingKey),
            title=track.title,
            artist=self._get_track_artist(track),
            album=track.parentTitle,
            track=track,
        )
