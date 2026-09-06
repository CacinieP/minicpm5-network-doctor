import socket
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def isolated_proxy_settings(monkeypatch):
    """Keep tests independent of the runner's credentials and system proxy settings."""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    for key in ("MINICPM_BASE_URL", "MINICPM_MODEL", "MINICPM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """DNS can bypass socket.socket; every diagnostic lookup must be mocked."""

    def unexpected_lookup(*args, **kwargs):
        pytest.fail("Unmocked DNS lookup: tests must not use the network or model servers")

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(socket, name, unexpected_lookup)
