from __future__ import annotations

import socket

import pytest

from minicpm_network_doctor.tools import (
    TOOLS,
    _classify_address,
    execute_tool,
    inspect_proxy_environment,
    openai_tools,
    resolve_dns,
)


def test_tool_schemas_match_registry() -> None:
    schemas = openai_tools()
    assert [item["function"]["name"] for item in schemas] == list(TOOLS)
    assert all(item["function"]["parameters"]["additionalProperties"] is False for item in schemas)


def test_resolve_localhost() -> None:
    result = resolve_dns("localhost")
    assert result["ok"] is True
    assert result["addresses"]
    assert {item["family"] for item in result["addresses"]} <= {"IPv4", "IPv6"}


def test_invalid_host_is_rejected_without_network_access() -> None:
    result = execute_tool("resolve_dns", {"host": "https://example.com"})
    assert result["ok"] is False
    assert result["error_type"] == "ValueError"


def test_invalid_port_is_rejected() -> None:
    result = execute_tool("test_tcp", {"host": "localhost", "port": 70000})
    assert result["ok"] is False
    assert result["error_type"] == "ValueError"


def test_http_rejects_credentials() -> None:
    result = execute_tool("test_http", {"url": "https://user:secret@example.com/"})
    assert result["ok"] is False
    assert "credentials" in result["error"]


def test_unknown_tool_is_rejected() -> None:
    assert execute_tool("run_shell", {}) == {
        "ok": False,
        "error": "unknown_tool",
        "tool": "run_shell",
    }


def test_proxy_credentials_are_redacted(monkeypatch) -> None:
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:secret@127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    result = inspect_proxy_environment()

    assert result["proxy_variables"]["HTTPS_PROXY"] == "http://***@127.0.0.1:7890"
    assert "secret" not in str(result)
    assert result["no_proxy_entries"] == ["localhost", "127.0.0.1"]


def test_invalid_proxy_port_is_hidden(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:not-a-port")

    result = inspect_proxy_environment()

    assert result["proxy_variables"]["HTTP_PROXY"] == "[set; invalid port hidden]"


@pytest.mark.parametrize(
    "address,expected",
    [
        # RFC 2544 benchmarking range reused by Clash/Mihomo fake-ip pools.
        ("198.18.0.190", "fake-ip"),
        ("198.19.5.5", "fake-ip"),
        # RFC 6598 carrier-grade NAT / Tailscale overlay.
        ("100.64.0.1", "cg nat"),
        # Private ranges.
        ("10.0.0.1", "private"),
        ("172.16.4.20", "private"),
        ("192.168.1.1", "private"),
        # Loopback and link-local.
        ("127.0.0.1", "loopback"),
        ("::1", "loopback"),
        ("169.254.169.254", "link-local"),
        # Documentation / test ranges.
        ("192.0.2.1", "documentation"),
        ("203.0.113.5", "documentation"),
        # A well-known public address.
        ("8.8.8.8", "public"),
        ("93.184.216.34", "public"),
    ],
)
def test_classify_address_labels_known_ranges(address: str, expected: str) -> None:
    assert _classify_address(address).startswith(expected)


def test_classify_address_rejects_garbage() -> None:
    assert _classify_address("not-an-ip") == "invalid"


def test_resolve_dns_flags_fake_ip_addresses(monkeypatch) -> None:
    """When DNS returns reserved-range addresses, observations must warn the model."""
    fake_records = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("198.18.0.190", 443)),
    ]

    def fake_getaddrinfo(host, port, *, type):  # noqa: ARG001
        return fake_records

    monkeypatch.setattr("minicpm_network_doctor.tools.socket.getaddrinfo", fake_getaddrinfo)

    result = resolve_dns("registry.npmjs.org")

    assert result["ok"] is True
    assert result["addresses"][0]["classification"].startswith("fake-ip")
    assert any("fake-ip" in obs for obs in result["observations"])
    assert any("Clash" in obs or "Mihomo" in obs for obs in result["observations"])


def test_resolve_dns_confirms_public_addresses(monkeypatch) -> None:
    """When all addresses are public, observations should say so without warning."""
    public_records = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
    ]

    monkeypatch.setattr(
        "minicpm_network_doctor.tools.socket.getaddrinfo",
        lambda host, port, *, type: public_records,  # noqa: ARG005
    )

    result = resolve_dns("example.com")

    assert result["observations"] == ["All resolved addresses are public routable IPs."]


def _http_response(status=200, url="https://example.com/", server="nginx"):
    import types

    return types.SimpleNamespace(
        status=status,
        geturl=lambda: url,
        headers={"Server": server, "Content-Type": "text/html"},
    )


def test_test_tcp_reports_peer_address(monkeypatch) -> None:

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getpeername(self):
            return ("93.184.216.34", 443)

    captured = {}

    def fake_connect(address, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return _Conn()

    monkeypatch.setattr("minicpm_network_doctor.tools.socket.create_connection", fake_connect)

    from minicpm_network_doctor.tools import test_tcp

    result = test_tcp("example.com", 443, timeout=3.0)

    assert result["ok"] is True
    assert result["peer_address"] == "93.184.216.34"
    assert captured["address"] == ("example.com", 443)
    assert captured["timeout"] == 3.0
    assert result["duration_ms"] >= 0


def test_test_http_reports_status_without_raising_on_http_error(monkeypatch) -> None:
    """A 4xx/5xx is a result, not an exception: the model needs the status."""
    import urllib.error
    import urllib.request

    calls = []

    class _Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_method(), timeout))
            raise urllib.error.HTTPError(
                request.full_url, 503, "Service Unavailable", {"Server": "gateway"}, None
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda handler=None: _Opener())

    from minicpm_network_doctor.tools import test_http

    result = test_http("https://registry.example.org/health", timeout=4.0)

    assert result["ok"] is True  # the check ran; the outcome is the status
    assert result["status"] == 503
    assert result["server"] == "gateway"
    assert calls[0][0] == "https://registry.example.org/health"
    assert calls[0][1] == "HEAD"


def test_test_http_happy_path(monkeypatch) -> None:
    import contextlib
    import urllib.request

    class _Opener:
        def open(self, request, timeout):
            return contextlib.nullcontext(
                _http_response(200, "https://example.com/index.html", "cloudfront")
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda handler=None: _Opener())

    from minicpm_network_doctor.tools import test_http

    result = test_http("https://example.com")

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["final_url"] == "https://example.com/index.html"
    assert result["server"] == "cloudfront"


def test_inspect_tls_summarizes_certificate(monkeypatch) -> None:
    import datetime
    import types

    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)
    not_after = expiry.strftime("%b %d %H:%M:%S %Y GMT")

    wrapped = types.SimpleNamespace(
        getpeercert=lambda: {
            "subject": ((("commonName", "example.com"),),),
            "issuer": ((("organizationName", "Let's Encrypt"),),),
            "notAfter": not_after,
        },
        version=lambda: "TLSv1.3",
        cipher=lambda: ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
    )

    class _Context:
        def wrap_socket(self, raw, server_hostname=None):
            assert server_hostname == "example.com"
            return _CtxWrap(wrapped)

    class _CtxWrap:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            return self._inner

        def __exit__(self, *exc):
            return False

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "minicpm_network_doctor.tools.socket.create_connection",
        lambda address, timeout: _Raw(),
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.tools.ssl.create_default_context",
        lambda: _Context(),
    )

    from minicpm_network_doctor.tools import inspect_tls

    result = inspect_tls("example.com", 443, timeout=4.0)

    assert result["ok"] is True
    assert result["protocol"] == "TLSv1.3"
    assert result["cipher"] == "TLS_AES_256_GCM_SHA384"
    assert result["subject"] == "commonName=example.com"
    assert result["issuer"] == "organizationName=Let's Encrypt"
    assert 80 <= result["days_remaining"] <= 91


def test_inspect_tls_handles_certificate_without_expiry(monkeypatch) -> None:
    import types

    wrapped = types.SimpleNamespace(
        getpeercert=lambda: {"subject": (), "issuer": ()},
        version=lambda: "TLSv1.2",
        cipher=lambda: ("ECDHE-RSA-AES128-GCM-SHA256", "TLSv1.2", 128),
    )

    class _CtxWrap:
        def __enter__(self):
            return wrapped

        def __exit__(self, *exc):
            return False

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "minicpm_network_doctor.tools.socket.create_connection",
        lambda address, timeout: _Raw(),
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.tools.ssl.create_default_context",
        lambda: type(
            "Ctx", (), {"wrap_socket": lambda self, raw, server_hostname=None: _CtxWrap()}
        )(),
    )

    from minicpm_network_doctor.tools import inspect_tls

    result = inspect_tls("example.com")

    assert result["ok"] is True
    assert result["expires_at"] is None
    assert result["days_remaining"] is None


def test_execute_tool_wraps_handler_exceptions(monkeypatch) -> None:
    from minicpm_network_doctor import tools as tools_mod

    def _boom(**kwargs):
        raise KeyError("missing")

    monkeypatch.setitem(tools_mod.TOOLS, "boom", tools_mod.ToolSpec("boom", "boom", {}, _boom))
    result = tools_mod.execute_tool("boom", {})

    assert result["ok"] is False
    assert result["error_type"] == "KeyError"
