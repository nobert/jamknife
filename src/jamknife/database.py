"""SQLite database models and session management."""

import logging
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


class DownloadStatus(StrEnum):
    """Status of an album download job."""

    PENDING = "pending"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncStatus(StrEnum):
    """Status of a playlist sync job."""

    PENDING = "pending"
    FETCHING = "fetching"
    MATCHING = "matching"
    DOWNLOADING = "downloading"
    CREATING_PLAYLIST = "creating_playlist"
    COMPLETED = "completed"
    FAILED = "failed"


class ListenBrainzPlaylist(Base):
    """Tracked ListenBrainz playlist."""

    __tablename__ = "listenbrainz_playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mbid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    creator: Mapped[str] = mapped_column(String(255), nullable=False)
    created_for: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_daily: Mapped[bool] = mapped_column(Boolean, default=False)
    is_weekly: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(datetime.UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(datetime.UTC),
        onupdate=lambda: datetime.now(datetime.UTC),
    )

    # Relationships
    sync_jobs: Mapped[list["PlaylistSyncJob"]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan"
    )


class PlaylistSyncJob(Base):
    """A job to sync a ListenBrainz playlist to Plex."""

    __tablename__ = "playlist_sync_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listenbrainz_playlists.id"), nullable=False
    )
    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus), default=SyncStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracks_total: Mapped[int] = mapped_column(Integer, default=0)
    tracks_matched: Mapped[int] = mapped_column(Integer, default=0)
    tracks_missing: Mapped[int] = mapped_column(Integer, default=0)
    plex_playlist_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(datetime.UTC)
    )

    # Relationships
    playlist: Mapped["ListenBrainzPlaylist"] = relationship(back_populates="sync_jobs")
    track_matches: Mapped[list["TrackMatch"]] = relationship(
        back_populates="sync_job", cascade="all, delete-orphan"
    )


class TrackMatch(Base):
    """Mapping of a ListenBrainz track to Plex or download status."""

    __tablename__ = "track_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("playlist_sync_jobs.id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # ListenBrainz track info
    recording_mbid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    track_name: Mapped[str] = mapped_column(String(500), nullable=False)
    artist_name: Mapped[str] = mapped_column(String(500), nullable=False)
    album_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    release_mbid: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # YouTube Music resolution
    ytmusic_album_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ytmusic_album_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Plex matching
    plex_rating_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    matched_in_plex: Mapped[bool] = mapped_column(Boolean, default=False)

    # Download tracking
    album_download_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("album_downloads.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(datetime.UTC)
    )

    # Relationships
    sync_job: Mapped["PlaylistSyncJob"] = relationship(back_populates="track_matches")
    album_download: Mapped[Optional["AlbumDownload"]] = relationship(
        back_populates="track_matches"
    )


class AlbumDownload(Base):
    """Queued or completed album download from Yubal."""

    __tablename__ = "album_downloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ytmusic_album_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    ytmusic_album_url: Mapped[str] = mapped_column(String(500), nullable=False)
    album_name: Mapped[str] = mapped_column(String(500), nullable=False)
    artist_name: Mapped[str] = mapped_column(String(500), nullable=False)

    # Yubal job tracking
    yubal_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[DownloadStatus] = mapped_column(
        Enum(DownloadStatus), default=DownloadStatus.PENDING, nullable=False
    )
    progress: Mapped[float] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    queued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(datetime.UTC)
    )

    # Relationships
    track_matches: Mapped[list["TrackMatch"]] = relationship(
        back_populates="album_download"
    )


class MBIDPlexMapping(Base):
    """Cached mapping of MusicBrainz recording MBID to Plex rating key."""

    __tablename__ = "mbid_plex_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_mbid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    plex_rating_key: Mapped[str] = mapped_column(String(50), nullable=False)
    track_title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist_name: Mapped[str] = mapped_column(String(500), nullable=False)
    album_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(datetime.UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(datetime.UTC),
        onupdate=lambda: datetime.now(datetime.UTC),
    )


def init_database(db_path: Path) -> sessionmaker:
    """Initialize database and return session factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    logger.info("Database initialized at %s", db_path)
    return sessionmaker(bind=engine)
