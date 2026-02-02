"""YouTube Music resolver for finding album URLs."""

import logging
from dataclasses import dataclass

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)


@dataclass
class AlbumInfo:
    """Information about a YouTube Music album."""

    album_id: str
    title: str
    artist: str
    url: str
    year: str | None = None
    track_count: int | None = None


class YTMusicResolver:
    """Resolver for finding YouTube Music album URLs for tracks."""

    # YouTube Music album URL format
    ALBUM_URL_TEMPLATE = "https://music.youtube.com/playlist?list={browse_id}"
    BROWSE_URL_TEMPLATE = "https://music.youtube.com/browse/{browse_id}"

    def __init__(self):
        """Initialize the YouTube Music client."""
        self._ytm = YTMusic()

    def find_album_for_track(
        self, track_title: str, artist_name: str, album_name: str | None = None
    ) -> AlbumInfo | None:
        """Find the album containing a track on YouTube Music.

        Uses a multi-stage search strategy:
        1. If album name provided, search for album directly
        2. Search for the song and get its album info
        3. Search for artist and browse albums

        Args:
            track_title: Title of the track.
            artist_name: Name of the artist.
            album_name: Optional album name for more precise matching.

        Returns:
            AlbumInfo if found, None otherwise.
        """
        # Strategy 1: Search for album directly if name is provided
        if album_name:
            album = self._search_album(album_name, artist_name)
            if album:
                return album

        # Strategy 2: Search for the song and get album info
        album = self._search_song_get_album(track_title, artist_name)
        if album:
            return album

        # Strategy 3: Search for artist's discography
        if album_name:
            album = self._search_artist_albums(artist_name, album_name)
            if album:
                return album

        return None

    def _search_album(self, album_name: str, artist_name: str) -> AlbumInfo | None:
        """Search for an album by name and artist."""
        try:
            query = f"{artist_name} {album_name}"
            results = self._ytm.search(query, filter="albums", limit=10)

            for result in results:
                if result.get("resultType") != "album":
                    continue

                result_title = result.get("title", "")
                result_artists = self._get_artist_names(result)

                if self._names_match(result_title, album_name) and self._artist_matches(
                    result_artists, artist_name
                ):
                    browse_id = result.get("browseId")
                    if browse_id:
                        return self._create_album_info(result, browse_id)

        except Exception as e:
            logger.debug("Album search failed for '%s': %s", album_name, e)

        return None

    def _search_song_get_album(
        self, track_title: str, artist_name: str
    ) -> AlbumInfo | None:
        """Search for a song and extract its album information."""
        try:
            query = f"{artist_name} {track_title}"
            results = self._ytm.search(query, filter="songs", limit=10)

            for result in results:
                if result.get("resultType") != "song":
                    continue

                result_title = result.get("title", "")
                result_artists = self._get_artist_names(result)

                if self._names_match(result_title, track_title) and self._artist_matches(
                    result_artists, artist_name
                ):
                    # Get album info from the song result
                    album_info = result.get("album")
                    if album_info and album_info.get("id"):
                        return self._fetch_album_details(album_info.get("id"))

        except Exception as e:
            logger.debug("Song search failed for '%s': %s", track_title, e)

        return None

    def _search_artist_albums(
        self, artist_name: str, album_name: str
    ) -> AlbumInfo | None:
        """Search for an artist and browse their albums."""
        try:
            results = self._ytm.search(artist_name, filter="artists", limit=5)

            for result in results:
                if result.get("resultType") != "artist":
                    continue

                result_name = result.get("artist", "")
                if not self._names_match(result_name, artist_name):
                    continue

                browse_id = result.get("browseId")
                if not browse_id:
                    continue

                # Get artist's albums
                try:
                    artist_data = self._ytm.get_artist(browse_id)
                    albums_data = artist_data.get("albums", {})
                    albums = albums_data.get("results", [])

                    for album in albums:
                        if self._names_match(album.get("title", ""), album_name):
                            album_browse_id = album.get("browseId")
                            if album_browse_id:
                                return self._fetch_album_details(album_browse_id)
                except Exception as e:
                    logger.debug("Failed to get artist albums: %s", e)

        except Exception as e:
            logger.debug("Artist search failed for '%s': %s", artist_name, e)

        return None

    def _fetch_album_details(self, browse_id: str) -> AlbumInfo | None:
        """Fetch full album details by browse ID."""
        try:
            album = self._ytm.get_album(browse_id)
            if not album:
                return None

            title = album.get("title", "Unknown Album")
            artists = self._get_artist_names(album)
            artist = artists[0] if artists else "Unknown Artist"
            year = album.get("year")
            track_count = album.get("trackCount")

            # Get the playlist ID for the album URL
            # Albums use OLAK5uy_ format for playlist IDs
            audio_playlist_id = album.get("audioPlaylistId")
            if audio_playlist_id:
                url = self.ALBUM_URL_TEMPLATE.format(browse_id=audio_playlist_id)
            else:
                url = self.BROWSE_URL_TEMPLATE.format(browse_id=browse_id)

            return AlbumInfo(
                album_id=browse_id,
                title=title,
                artist=artist,
                url=url,
                year=year,
                track_count=track_count,
            )

        except Exception as e:
            logger.debug("Failed to fetch album details for '%s': %s", browse_id, e)
            return None

    def _create_album_info(self, result: dict, browse_id: str) -> AlbumInfo:
        """Create AlbumInfo from search result."""
        title = result.get("title", "Unknown Album")
        artists = self._get_artist_names(result)
        artist = artists[0] if artists else "Unknown Artist"
        year = result.get("year")

        # Fetch full details to get the audio playlist ID
        full_album = self._fetch_album_details(browse_id)
        if full_album:
            return full_album

        # Fallback to browse URL
        url = self.BROWSE_URL_TEMPLATE.format(browse_id=browse_id)
        return AlbumInfo(
            album_id=browse_id,
            title=title,
            artist=artist,
            url=url,
            year=year,
        )

    def _get_artist_names(self, data: dict) -> list[str]:
        """Extract artist names from various result formats."""
        artists = []

        # Try 'artists' field (list of dicts)
        if "artists" in data:
            for artist in data["artists"]:
                if isinstance(artist, dict):
                    name = artist.get("name") or artist.get("artist")
                    if name:
                        artists.append(name)
                elif isinstance(artist, str):
                    artists.append(artist)

        # Try 'artist' field (single string)
        if not artists and "artist" in data:
            artist = data["artist"]
            if isinstance(artist, str):
                artists.append(artist)
            elif isinstance(artist, dict):
                name = artist.get("name")
                if name:
                    artists.append(name)

        return artists

    def _names_match(self, name1: str, name2: str) -> bool:
        """Check if two names match (case-insensitive, normalized)."""
        if not name1 or not name2:
            return False
        n1 = self._normalize_name(name1)
        n2 = self._normalize_name(name2)
        # Check exact match or containment
        return n1 == n2 or n1 in n2 or n2 in n1

    def _artist_matches(self, artists: list[str], target: str) -> bool:
        """Check if any artist in the list matches the target."""
        target_norm = self._normalize_name(target)
        for artist in artists:
            artist_norm = self._normalize_name(artist)
            if artist_norm == target_norm or artist_norm in target_norm or target_norm in artist_norm:
                return True
        return False

    def _normalize_name(self, name: str) -> str:
        """Normalize a name for comparison."""
        name = name.lower().strip()
        # Remove "the " prefix
        if name.startswith("the "):
            name = name[4:]
        # Remove common punctuation
        for char in ["'", '"', ".", ",", "!", "?", "(", ")", "[", "]", "-", ":"]:
            name = name.replace(char, "")
        # Normalize whitespace
        name = " ".join(name.split())
        return name
