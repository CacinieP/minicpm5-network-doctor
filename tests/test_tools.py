from __future__ import annotations

from minicpm5_network_doctor.tools import (
    TOOLS,
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
