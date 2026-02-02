# syntax=docker/dockerfile:1

# Build stage
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Build wheel
RUN pip wheel --no-deps --wheel-dir /build/wheels .


# Runtime stage
FROM python:3.11-slim as runtime

# Create non-root user for security
RUN groupadd --gid 1000 jamknife && \
    useradd --uid 1000 --gid 1000 --create-home jamknife

WORKDIR /app

# Install the wheel and dependencies
COPY --from=builder /build/wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -f /tmp/*.whl

# Create data directories
RUN mkdir -p /data /downloads && \
    chown -R jamknife:jamknife /data /downloads

# Switch to non-root user
USER jamknife

# Environment variables with defaults
ENV LISTENBRAINZ_USERNAME="" \
    LISTENBRAINZ_TOKEN="" \
    PLEX_URL="" \
    PLEX_TOKEN="" \
    PLEX_MUSIC_LIBRARY="Music" \
    YUBAL_URL="http://yubal:8000" \
    DATA_DIR="/data" \
    DOWNLOADS_DIR="/downloads" \
    WEB_HOST="0.0.0.0" \
    WEB_PORT="8080"

# Expose web port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/api/status', timeout=5)" || exit 1

# Run the application
ENTRYPOINT ["jamknife"]
