#!/usr/bin/env python3
"""
Test connection to ListenBrainz API to diagnose network/TLS issues.

This script helps diagnose connection problems by:
1. Testing basic HTTPS connectivity to api.listenbrainz.org
2. Showing detailed SSL/TLS certificate information
3. Testing the ListenBrainz API with retry logic
"""

import ssl
import socket
import sys
from pathlib import Path

# Add parent directory to path to import jamknife modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
from jamknife.clients.listenbrainz import ListenBrainzClient


def test_basic_connection():
    """Test basic TCP connection to ListenBrainz."""
    print("🔌 Testing basic TCP connection to api.listenbrainz.org:443...")
    try:
        sock = socket.create_connection(("api.listenbrainz.org", 443), timeout=10)
        sock.close()
        print("✅ TCP connection successful\n")
        return True
    except Exception as e:
        print(f"❌ TCP connection failed: {e}\n")
        return False


def test_ssl_connection():
    """Test SSL/TLS connection and show certificate info."""
    print("🔒 Testing SSL/TLS connection to api.listenbrainz.org...")
    try:
        context = ssl.create_default_context()
        with socket.create_connection(("api.listenbrainz.org", 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname="api.listenbrainz.org") as ssock:
                cert = ssock.getpeercert()
                print(f"✅ SSL/TLS connection successful")
                print(f"   Protocol: {ssock.version()}")
                print(f"   Cipher: {ssock.cipher()}")
                if cert:
                    print(f"   Subject: {cert.get('subject')}")
                    print(f"   Issuer: {cert.get('issuer')}")
                    print(f"   Valid until: {cert.get('notAfter')}")
                print()
                return True
    except Exception as e:
        print(f"❌ SSL/TLS connection failed: {e}\n")
        return False


def test_http_request():
    """Test simple HTTP request with httpx."""
    print("🌐 Testing HTTP request to ListenBrainz API...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get("https://api.listenbrainz.org/1/stats/sitewide/artists")
            response.raise_for_status()
            print(f"✅ HTTP request successful (status {response.status_code})\n")
            return True
    except Exception as e:
        print(f"❌ HTTP request failed: {e}\n")
        return False


def test_listenbrainz_client():
    """Test ListenBrainz client with retry logic."""
    print("🎵 Testing ListenBrainz client with retry logic...")
    try:
        client = ListenBrainzClient(max_retries=3, timeout=30.0)
        # Test a simple public endpoint
        client._get("/stats/sitewide/artists", params={"range": "all_time"})
        print("✅ ListenBrainz client request successful\n")
        client.close()
        return True
    except Exception as e:
        print(f"❌ ListenBrainz client request failed: {e}\n")
        return False


def main():
    """Run all connection tests."""
    print("=" * 70)
    print("ListenBrainz Connection Diagnostic Tool")
    print("=" * 70)
    print()

    results = []
    results.append(("TCP Connection", test_basic_connection()))
    results.append(("SSL/TLS Connection", test_ssl_connection()))
    results.append(("HTTP Request", test_http_request()))
    results.append(("ListenBrainz Client", test_listenbrainz_client()))

    print("=" * 70)
    print("Summary:")
    print("=" * 70)
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("✅ All tests passed! Connection to ListenBrainz is working.")
    else:
        print("❌ Some tests failed. Possible issues:")
        print("   - Firewall blocking HTTPS connections")
        print("   - DNS resolution issues")
        print("   - SSL/TLS certificate validation problems")
        print("   - Network connectivity problems")
        print()
        print("If running in Docker, ensure the container has network access.")
        print("If behind a proxy, set HTTP_PROXY and HTTPS_PROXY environment variables.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
