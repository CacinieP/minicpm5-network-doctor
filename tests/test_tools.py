from __future__ import annotations

import email.message
import io
import math
import socket
import urllib.error
import urllib.request

import pytest

from minicpm_network_doctor.tools import (
    TOOLS,
    _classify_address,
    _ScopedRedirectHandler,
    _validate_host,
    _validate_timeout,
    _validate_url,
    execute_tool,
    inspect_hosts_file,
    inspect_proxy_environment,
    inspect_tls,
    openai_tools,
    resolve_dns,
    system_network_context,
)
from minicpm_network_doctor.tools import (
    test_http as run_http_test,
)
from minicpm_network_doctor.tools import (
    test_tcp as run_tcp_test,
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


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "a" * 254,
        "bad host",
        "example.com/path",
        "example.com:443",
        "-bad.example",
        "bad-.example",
        "127.0.0.1%en0",
        "fe80::1%bad zone",
    ],
)
def test_host_validation_rejects_invalid_values(host) -> None:
    with pytest.raises(ValueError):
        _validate_host(host)


def test_host_validation_accepts_idna_and_bracketed_ipv6() -> None:
    assert _validate_host("例子.测试") == "xn--fsqu00a.xn--0zwm56d"
    assert _validate_host("[::1]") == "::1"


def test_timeout_is_type_checked_and_clamped() -> None:
    with pytest.raises(ValueError):
        _validate_timeout(True)
    assert _validate_timeout(0.01) == 0.5
    assert _validate_timeout(99) == 10.0
    for value in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError, match="finite"):
            _validate_timeout(value)


def test_invalid_port_is_rejected() -> None:
    result = execute_tool("test_tcp", {"host": "localhost", "port": 70000})
    assert result["ok"] is False
    assert result["error_type"] == "ValueError"


def test_http_rejects_credentials() -> None:
    result = execute_tool("test_http", {"url": "https://user:secret@example.com/"})
    assert result["ok"] is False
    assert "credentials" in result["error"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com\\@evil.test/",
        "https://example.com/line\nbreak",
        "https://example.com:99999/",
        "https://example.com/" + "x" * 2_048,
    ],
)
def test_http_rejects_unsafe_or_invalid_urls(url) -> None:
    with pytest.raises(ValueError):
        _validate_url(url)


def test_url_validation_normalizes_idna_and_strips_fragment() -> None:
    assert _validate_url("HTTPS://例子.测试:443/a?b=1#secret") == (
        "https://xn--fsqu00a.xn--0zwm56d:443/a?b=1"
    )


def test_http_redirect_handler_blocks_unreported_host() -> None:
    handler = _ScopedRedirectHandler("example.com")
    request = urllib.request.Request("https://example.com/start", method="HEAD")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://other.example/landing",
    )

    assert redirected is None
    assert handler.blocked_target_host == "other.example"


def test_http_redirect_handler_blocks_unreported_port() -> None:
    handler = _ScopedRedirectHandler("example.com", {80, 443})
    request = urllib.request.Request("https://example.com/start", method="HEAD")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://example.com:2375/landing",
    )

    assert redirected is None
    assert handler.blocked_target_host == "example.com"
    assert handler.blocked_target_port == 2375


def test_unknown_tool_is_rejected() -> None:
    assert execute_tool("run_shell", {}) == {
        "ok": False,
        "error": "unknown_tool",
        "tool": "run_shell",
    }


def test_execute_tool_rejects_non_object_arguments() -> None:
    assert execute_tool("resolve_dns", []) == {
        "ok": False,
        "error": "arguments_must_be_an_object",
        "tool": "resolve_dns",
    }


def test_execute_tool_enforces_reported_target_scope() -> None:
    result = execute_tool(
        "resolve_dns",
        {"host": "other.example"},
        allowed_hosts=frozenset({"example.com"}),
    )

    assert result["ok"] is False
    assert result["error"] == "target_out_of_scope"
    assert result["target"] == "other.example"


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
    monkeypatch.setattr(
        "minicpm_network_doctor.tools.urllib.request.getproxies",
        lambda: {
            "https": "http://system:secret@127.0.0.1:7892",
            "no": "localhost,127.0.0.1",
        },
    )

    result = inspect_proxy_environment()

    assert result["proxy_variables"]["HTTPS_PROXY"] == "http://***@127.0.0.1:7890"
    assert "secret" not in str(result)
    assert result["no_proxy_entries"] == ["localhost", "127.0.0.1"]
    assert result["effective_proxies"]["HTTPS"] == "http://***@127.0.0.1:7892"


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


def test_inspect_hosts_file_returns_only_requested_entries(monkeypatch, tmp_path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "127.0.0.1 localhost\n"
        "198.18.0.42 registry.example.org registry\n"
        "10.0.0.4 internal.example.org # private\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("minicpm_network_doctor.tools._hosts_file_path", lambda: hosts)

    result = inspect_hosts_file("registry.example.org")

    assert result["overridden"] is True
    assert result["entries"] == [
        {
            "address": "198.18.0.42",
            "classification": "fake-ip (198.18.0.0/15)",
            "aliases": ["registry.example.org"],
            "line": 2,
        }
    ]
    assert "internal.example.org" not in str(result)


def test_inspect_hosts_file_reports_no_override(monkeypatch, tmp_path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    monkeypatch.setattr("minicpm_network_doctor.tools._hosts_file_path", lambda: hosts)

    result = inspect_hosts_file("example.com")

    assert result["ok"] is True
    assert result["overridden"] is False
    assert result["entries"] == []


def test_http_falls_back_to_ranged_get_when_head_is_rejected(monkeypatch) -> None:
    response_headers = email.message.Message()
    response_headers["Content-Type"] = "text/plain"
    head_error = urllib.error.HTTPError(
        "https://example.com/",
        405,
        "Method Not Allowed",
        response_headers,
        io.BytesIO(b""),
    )

    class Response:
        status = 206
        headers = response_headers

        def geturl(self):
            return "https://example.com/final"

        def close(self):
            return None

    class Opener:
        def __init__(self) -> None:
            self.requests = []

        def open(self, request, timeout):  # noqa: ARG002
            self.requests.append(request)
            if len(self.requests) == 1:
                raise head_error
            return Response()

    opener = Opener()
    monkeypatch.setattr(
        "minicpm_network_doctor.tools.urllib.request.build_opener", lambda *args: opener
    )

    result = run_http_test("https://example.com/")

    assert result["ok"] is True
    assert result["method"] == "GET"
    assert result["status"] == 206
    assert result["head_fallback_reason"] == "HEAD returned HTTP 405"
    assert opener.requests[1].get_header("Range") == "bytes=0-0"


def test_resolve_dns_deduplicates_and_explains_non_public_ranges(monkeypatch) -> None:
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.64.0.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.64.0.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.0.2.1", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2001:db8::1", 443, 0, 0)),
    ]
    monkeypatch.setattr(
        "minicpm_network_doctor.tools.socket.getaddrinfo",
        lambda *args, **kwargs: records,
    )

    result = resolve_dns("example.com")

    assert len(result["addresses"]) == 4
    observations = " ".join(result["observations"])
    assert "overlay" in observations
    assert "split-horizon" in observations
    assert "documentation/test" in observations


def test_tcp_reports_peer_and_clamped_timeout(monkeypatch) -> None:
    captured = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def getpeername(self):
            return ("93.184.216.34", 443)

    def connect(address, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return Connection()

    monkeypatch.setattr("minicpm_network_doctor.tools.socket.create_connection", connect)

    result = run_tcp_test("example.com", 443, timeout=99)

    assert captured == {"address": ("example.com", 443), "timeout": 10.0}
    assert result["peer_address"] == "93.184.216.34"


def test_http_reports_non_fallback_http_error(monkeypatch) -> None:
    response_headers = email.message.Message()
    response_headers["Server"] = "test"
    error = urllib.error.HTTPError(
        "https://example.com/missing", 404, "Not Found", response_headers, io.BytesIO(b"")
    )

    class Opener:
        def open(self, request, timeout):  # noqa: ARG002
            raise error

    monkeypatch.setattr(
        "minicpm_network_doctor.tools.urllib.request.build_opener", lambda *args: Opener()
    )

    result = run_http_test("https://example.com/missing", use_environment_proxy=False)

    assert result["ok"] is True
    assert result["status"] == 404
    assert result["method"] == "HEAD"
    assert result["used_environment_proxy"] is False


def test_inspect_tls_returns_bounded_certificate_details(monkeypatch) -> None:
    certificate = {
        "subject": ((("commonName", "example.com"),),),
        "issuer": ((("organizationName", "Test CA"),),),
        "subjectAltName": tuple(("DNS", f"host-{index}.example.com") for index in range(25)),
        "notAfter": "Jan  1 00:00:00 2030 GMT",
    }

    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class Wrapped(Raw):
        def getpeercert(self):
            return certificate

        def version(self):
            return "TLSv1.3"

        def cipher(self):
            return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

        def selected_alpn_protocol(self):
            return "h2"

    class Context:
        def set_alpn_protocols(self, protocols):
            assert protocols == ["h2", "http/1.1"]

        def wrap_socket(self, raw, server_hostname):  # noqa: ARG002
            assert server_hostname == "example.com"
            return Wrapped()

    monkeypatch.setattr(
        "minicpm_network_doctor.tools.socket.create_connection", lambda *args, **kwargs: Raw()
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.tools.ssl.create_default_context", lambda: Context()
    )

    result = inspect_tls("example.com")

    assert result["protocol"] == "TLSv1.3"
    assert result["alpn_protocol"] == "h2"
    assert result["subject"] == "commonName=example.com"
    assert result["issuer"] == "organizationName=Test CA"
    assert len(result["subject_alt_names"]) == 20
    assert result["subject_alt_names_truncated"] is True
    assert result["days_remaining"] is not None


def test_system_context_includes_redacted_proxy(monkeypatch) -> None:
    monkeypatch.setattr("minicpm_network_doctor.tools.platform.system", lambda: "TestOS")
    monkeypatch.setattr("minicpm_network_doctor.tools.platform.release", lambda: "1.0")
    monkeypatch.setattr("minicpm_network_doctor.tools.platform.machine", lambda: "test64")
    monkeypatch.setenv("HTTP_PROXY", "http://user:pass@proxy.example:8080")

    result = system_network_context()

    assert result["operating_system"] == "TestOS"
    assert result["machine"] == "test64"
    assert result["proxy_environment"]["proxy_variables"]["HTTP_PROXY"] == (
        "http://***@proxy.example:8080"
    )
    assert "pass" not in str(result)


def test_hosts_file_size_limit_becomes_safe_tool_error(monkeypatch, tmp_path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("x" * 256_001, encoding="utf-8")
    monkeypatch.setattr("minicpm_network_doctor.tools._hosts_file_path", lambda: hosts)

    result = execute_tool("inspect_hosts_file", {"host": "example.com"})

    assert result["ok"] is False
    assert result["error_type"] == "ValueError"
    assert "unexpectedly large" in result["error"]
