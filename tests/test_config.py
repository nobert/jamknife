"""Tests for configuration module."""

import os
from pathlib import Path

import pytest

from jamknife.config import Config, get_config


def test_config_defaults():
    """Test default configuration values."""
    config = Config()
    assert config.web_host == "0.0.0.0"
    assert config.web_port == 8000
    assert config.plex_music_library == "Music"
    assert config.poll_interval == 60


def test_config_from_environment(monkeypatch):
    """Test configuration from environment variables."""
    monkeypatch.setenv("LISTENBRAINZ_USERNAME", "testuser")
    monkeypatch.setenv("LISTENBRAINZ_TOKEN", "testtoken")
    monkeypatch.setenv("PLEX_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "plextoken")
    monkeypatch.setenv("YUBAL_URL", "http://yubal:8000")
    monkeypatch.setenv("WEB_PORT", "9000")

    config = Config()
    assert config.listenbrainz_username == "testuser"
    assert config.listenbrainz_token == "testtoken"
    assert config.plex_url == "http://plex:32400"
    assert config.plex_token == "plextoken"
    assert config.yubal_url == "http://yubal:8000"
    assert config.web_port == 9000


def test_config_validation():
    """Test configuration validation."""
    config = Config()
    errors = config.validate()

    # Should have errors for missing required fields
    assert len(errors) > 0
    assert any("LISTENBRAINZ_USERNAME" in e for e in errors)
    assert any("PLEX_TOKEN" in e for e in errors)


def test_config_validation_complete(monkeypatch):
    """Test validation with complete configuration."""
    monkeypatch.setenv("LISTENBRAINZ_USERNAME", "testuser")
    monkeypatch.setenv("PLEX_TOKEN", "plextoken")
    monkeypatch.setenv("YUBAL_URL", "http://yubal:8000")

    config = Config()
    errors = config.validate()
    assert len(errors) == 0


def test_db_path():
    """Test database path property."""
    config = Config()
    expected_path = config.data_dir / "jamknife.db"
    assert config.db_path == expected_path


def test_get_config():
    """Test get_config singleton."""
    config = get_config()
    assert isinstance(config, Config)
