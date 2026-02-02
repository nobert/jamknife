# Connection Error Handling

## Problem

When syncing playlists, you may occasionally see connection errors like:
```
httpx.ConnectError: [Errno 104] Connection reset by peer
```

This typically happens during TLS handshake with the ListenBrainz API and is usually a transient network issue.

## Solution

The ListenBrainz client now includes automatic retry logic with exponential backoff:

- **3 retry attempts** by default
- **Exponential backoff**: waits 1s, 2s, 4s between retries
- **Detailed logging**: shows each retry attempt and failure reason

### How It Works

When a connection error occurs:
1. The client logs a warning with the error details
2. Waits with exponential backoff (1s → 2s → 4s)
3. Retries the request up to 3 times
4. Only fails if all retry attempts are exhausted

### Configuration

You can customize retry behavior when creating the client:

```python
from jamknife.clients.listenbrainz import ListenBrainzClient

# Default: 3 retries
client = ListenBrainzClient(token="your-token")

# Custom: 5 retries
client = ListenBrainzClient(token="your-token", max_retries=5)

# No retries
client = ListenBrainzClient(token="your-token", max_retries=1)
```

## Diagnostics

If you continue to experience connection issues, run the diagnostic script:

```bash
python scripts/test_listenbrainz_connection.py
```

This will test:
- Basic TCP connectivity to api.listenbrainz.org
- SSL/TLS connection and certificate validation
- HTTP requests via httpx
- ListenBrainz client with retry logic

### Common Issues

**Intermittent SSL/TLS resets** (most common):
- Symptom: Connection sometimes fails during TLS handshake
- Solution: Retry logic handles this automatically
- The diagnostic may show SSL test failing but HTTP/client tests passing

**Firewall blocking HTTPS**:
- Symptom: All connection tests fail
- Solution: Check firewall rules, allow outbound HTTPS to api.listenbrainz.org

**Docker network isolation**:
- Symptom: Tests pass on host but fail in container
- Solution: Ensure container has network access, check Docker network settings

**Proxy requirements**:
- Symptom: Connection times out or is refused
- Solution: Set `HTTP_PROXY` and `HTTPS_PROXY` environment variables

## Logging

The client logs retry attempts at WARNING level. To see these logs:

```python
import logging

logging.basicConfig(level=logging.WARNING)
```

Example log output:
```
WARNING:jamknife.clients.listenbrainz:Connection error on attempt 1/3 for https://api.listenbrainz.org/1/playlist/xyz: [Errno 104] Connection reset by peer
INFO:jamknife.clients.listenbrainz:Retrying in 1s...
```

## Related Files

- [src/jamknife/clients/listenbrainz.py](../src/jamknife/clients/listenbrainz.py) - Client with retry logic
- [scripts/test_listenbrainz_connection.py](test_listenbrainz_connection.py) - Diagnostic tool
- [tests/test_clients.py](../tests/test_clients.py) - Tests for retry behavior
