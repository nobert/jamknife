"""Application configuration from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Application configuration loaded from environment variables."""

    # ListenBrainz settings
    listenbrainz_username: str = field(
        default_factory=lambda: os.environ.get("LISTENBRAINZ_USERNAME", "")
    )
    listenbrainz_token: str = field(
        default_factory=lambda: os.environ.get("LISTENBRAINZ_TOKEN", "")
    )

    # Plex settings
    plex_url: str = field(
        default_factory=lambda: os.environ.get("PLEX_URL", "http://localhost:32400")
    )
    plex_token: str = field(
        default_factory=lambda: os.environ.get("PLEX_TOKEN", "")
    )
    plex_music_library: str = field(
        default_factory=lambda: os.environ.get("PLEX_MUSIC_LIBRARY", "Music")
    )

    # Yubal settings
    yubal_url: str = field(
        default_factory=lambda: os.environ.get("YUBAL_URL", "http://localhost:8080")
    )

    # Storage paths
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DATA_DIR", "/data"))
    )
    downloads_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DOWNLOADS_DIR", "/downloads"))
    )

    # Application settings
    poll_interval: int = field(
        default_factory=lambda: int(os.environ.get("POLL_INTERVAL", "60"))
    )
    web_host: str = field(
        default_factory=lambda: os.environ.get("WEB_HOST", "0.0.0.0")
    )
    web_port: int = field(
        default_factory=lambda: int(os.environ.get("WEB_PORT", "8000"))
    )

    @property
    def db_path(self) -> Path:
        """Path to SQLite database file."""
        return self.data_dir / "jamknife.db"

    def validate(self) -> list[str]:
        """Validate required configuration. Returns list of missing fields."""
        errors = []
        if not self.listenbrainz_username:
            errors.append("LISTENBRAINZ_USERNAME is required")
        if not self.plex_token:
            errors.append("PLEX_TOKEN is required")
        if not self.yubal_url:
            errors.append("YUBAL_URL is required")
        return errors


def get_config() -> Config:
    """Get application configuration singleton."""
    return Config()
