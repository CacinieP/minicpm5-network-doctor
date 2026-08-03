from __future__ import annotations

import socket

import pytest

from minicpm5_network_doctor.tools import (
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

    monkeypatch.setattr("minicpm5_network_doctor.tools.socket.getaddrinfo", fake_getaddrinfo)

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
        "minicpm5_network_doctor.tools.socket.getaddrinfo",
        lambda host, port, *, type: public_records,  # noqa: ARG005
    )

    result = resolve_dns("example.com")

    assert result["observations"] == ["All resolved addresses are public routable IPs."]
