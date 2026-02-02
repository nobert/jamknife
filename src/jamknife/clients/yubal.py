"""Yubal API client for submitting download jobs."""

import logging
from dataclasses import dataclass
from enum import StrEnum

import httpx

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Status of a Yubal download job."""

    PENDING = "pending"
    FETCHING_INFO = "fetching_info"
    DOWNLOADING = "downloading"
    IMPORTING = "importing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_finished(self) -> bool:
        """Check if the job has finished (success or failure)."""
        return self in (self.COMPLETED, self.FAILED, self.CANCELLED)

    @property
    def is_active(self) -> bool:
        """Check if the job is currently active."""
        return self in (self.PENDING, self.FETCHING_INFO, self.DOWNLOADING, self.IMPORTING)


@dataclass
class ContentInfo:
    """Information about downloaded content."""

    title: str
    artist: str
    year: int | None = None
    track_count: int | None = None
    kind: str | None = None  # "album", "playlist", or "track"


@dataclass
class Job:
    """A Yubal download job."""

    id: str
    url: str
    status: JobStatus
    progress: float
    content_info: ContentInfo | None = None
    error_message: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class YubalClient:
    """Client for the Yubal download API."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        """Initialize the Yubal client.

        Args:
            base_url: Base URL of the Yubal API (e.g., http://localhost:8080).
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def _get(self, endpoint: str) -> dict:
        """Make a GET request to the API."""
        url = f"{self._base_url}{endpoint}"
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, data: dict | None = None) -> dict:
        """Make a POST request to the API."""
        url = f"{self._base_url}{endpoint}"
        response = self._client.post(url, json=data)
        response.raise_for_status()
        return response.json()

    def _delete(self, endpoint: str) -> None:
        """Make a DELETE request to the API."""
        url = f"{self._base_url}{endpoint}"
        response = self._client.delete(url)
        response.raise_for_status()

    def health_check(self) -> bool:
        """Check if the Yubal API is healthy."""
        try:
            data = self._get("/health")
            return data.get("status") == "healthy"
        except Exception as e:
            logger.warning("Yubal health check failed: %s", e)
            return False

    def create_job(
        self, url: str, audio_format: str | None = None, max_items: int | None = None
    ) -> Job:
        """Create a new download job.

        Args:
            url: YouTube Music URL (album, playlist, or track).
            audio_format: Optional audio format (opus, mp3, m4a).
            max_items: Optional maximum number of tracks to download.

        Returns:
            The created Job object.

        Raises:
            httpx.HTTPStatusError: If the request fails (e.g., 409 for queue full).
        """
        data = {"url": url}
        if audio_format:
            data["audio_format"] = audio_format
        if max_items:
            data["max_items"] = max_items

        response = self._post("/jobs", data)
        job_id = response.get("id")

        # The create endpoint only returns the ID, fetch full job details
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Job:
        """Get the status of a download job.

        Args:
            job_id: The job ID.

        Returns:
            The Job object with current status.
        """
        # Jobs are returned via list endpoint, no individual get
        jobs = self.list_jobs()
        for job in jobs:
            if job.id == job_id:
                return job
        raise ValueError(f"Job not found: {job_id}")

    def list_jobs(self) -> list[Job]:
        """List all jobs.

        Returns:
            List of Job objects (oldest first).
        """
        data = self._get("/jobs")
        jobs = []
        for job_data in data.get("jobs", []):
            jobs.append(self._parse_job(job_data))
        return jobs

    def cancel_job(self, job_id: str) -> None:
        """Cancel a running or queued job.

        Args:
            job_id: The job ID to cancel.
        """
        self._post(f"/jobs/{job_id}/cancel")

    def delete_job(self, job_id: str) -> None:
        """Delete a finished job.

        Args:
            job_id: The job ID to delete.
        """
        self._delete(f"/jobs/{job_id}")

    def clear_finished_jobs(self) -> int:
        """Clear all completed/failed/cancelled jobs.

        Returns:
            Number of jobs cleared.
        """
        data = self._client.delete(f"{self._base_url}/jobs")
        data.raise_for_status()
        return data.json().get("cleared", 0)

    def wait_for_job(
        self, job_id: str, poll_interval: float = 2.0, timeout: float = 3600.0
    ) -> Job:
        """Wait for a job to complete.

        Args:
            job_id: The job ID to wait for.
            poll_interval: Seconds between status checks.
            timeout: Maximum seconds to wait.

        Returns:
            The completed Job object.

        Raises:
            TimeoutError: If the job doesn't complete within timeout.
        """
        import time

        start = time.time()
        while True:
            job = self.get_job(job_id)
            if job.status.is_finished:
                return job

            if time.time() - start > timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

            time.sleep(poll_interval)

    def _parse_job(self, data: dict) -> Job:
        """Parse a job from API response data."""
        content_info = None
        if data.get("content_info"):
            ci = data["content_info"]
            content_info = ContentInfo(
                title=ci.get("title", ""),
                artist=ci.get("artist", ""),
                year=ci.get("year"),
                track_count=ci.get("track_count"),
                kind=ci.get("kind"),
            )

        return Job(
            id=data["id"],
            url=data["url"],
            status=JobStatus(data.get("status", "pending")),
            progress=data.get("progress", 0.0),
            content_info=content_info,
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "YubalClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
