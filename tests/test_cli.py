import contextlib
import json
import types

import pytest

from minicpm_network_doctor.agent import DiagnosisResult, NetworkDoctorError, ToolEvent
from minicpm_network_doctor.cli import (
    _build_openai_client,
    _check_server_reachable,
    _is_loopback_base_url,
    _models_url,
    _print_progress,
    _read_prompt,
    _SameOriginRedirectHandler,
    _should_use_model_proxy,
    build_parser,
    main,
)


def test_list_tools_does_not_require_model_server(capsys) -> None:
    assert main(["--list-tools"]) == 0
    output = capsys.readouterr().out
    assert "resolve_dns" in output
    assert "inspect_tls" in output
    assert "run_shell" not in output


def test_list_tools_as_json(capsys) -> None:
    assert main(["--list-tools", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["type"] == "function"
    assert any(item["function"]["name"] == "inspect_hosts_file" for item in payload)


def _fake_response(status: int = 200, body: bytes = b""):
    ctx = types.SimpleNamespace(status=status, read=lambda limit: body[:limit])
    return contextlib.nullcontext(ctx)


def _patch_server_open(monkeypatch, open_request) -> None:
    opener = types.SimpleNamespace(open=open_request)
    monkeypatch.setattr(
        "minicpm_network_doctor.cli.urllib.request.build_opener",
        lambda *handlers: opener,  # noqa: ARG005
    )


def test_check_server_passes_when_reachable(monkeypatch) -> None:
    captured = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _fake_response(200)

    _patch_server_open(monkeypatch, fake_open)

    # Should not raise.
    _check_server_reachable("http://127.0.0.1:30000/v1")

    assert captured["url"] == "http://127.0.0.1:30000/v1/models"
    # Short probe timeout, not the full --timeout.
    assert captured["timeout"] <= 5


def test_check_server_reads_models_and_sends_auth(monkeypatch) -> None:
    body = json.dumps({"data": [{"id": "openbmb/MiniCPM5-1B"}, {"bad": True}]}).encode()
    captured = {}

    def fake_open(request, timeout):  # noqa: ARG001
        captured["authorization"] = request.get_header("Authorization")
        return _fake_response(body=body)

    _patch_server_open(monkeypatch, fake_open)

    result = _check_server_reachable("http://localhost:30000/v1/", api_key="secret")

    assert captured["authorization"] == "Bearer secret"
    assert result["models"] == ["openbmb/MiniCPM5-1B"]


@pytest.mark.parametrize(
    "url,message",
    [
        ("file:///tmp/server", "http or https"),
        ("http://user:secret@localhost/v1", "must not contain credentials"),
        ("http://localhost:99999/v1", "invalid model server URL"),
        ("http://localhost/v1?token=secret", "query or fragment"),
        ("http://localhost/v1#fragment", "query or fragment"),
    ],
)
def test_models_url_rejects_unsafe_or_invalid_urls(url, message) -> None:
    with pytest.raises(ValueError, match=message):
        _models_url(url)


def test_models_url_normalizes_route() -> None:
    assert _models_url("https://localhost:30000/v1/") == "https://localhost:30000/v1/models"


def test_loopback_model_endpoints_bypass_proxy_by_default() -> None:
    assert _is_loopback_base_url("http://127.0.0.1:11434/v1") is True
    assert _is_loopback_base_url("http://[::1]:11434/v1") is True
    assert _should_use_model_proxy("http://localhost:30000/v1", None) is False
    assert _should_use_model_proxy("https://api.example.com/v1", None) is True
    assert _should_use_model_proxy("http://localhost:30000/v1", True) is True


def test_model_redirect_handler_blocks_cross_origin() -> None:
    handler = _SameOriginRedirectHandler(("https", "api.example.com", 443))
    request = types.SimpleNamespace()

    assert (
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://other.example/v1/models",
        )
        is None
    )


def test_openai_client_receives_proxy_and_retry_policy(monkeypatch) -> None:
    captured = {}

    def fake_http_client(**kwargs):
        captured["http"] = kwargs
        return "client"

    def fake_openai(**kwargs):
        captured["openai"] = kwargs
        return "sdk"

    monkeypatch.setattr("openai.DefaultHttpxClient", fake_http_client)
    monkeypatch.setattr("openai.OpenAI", fake_openai)

    result = _build_openai_client(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        timeout=30,
        max_retries=0,
        use_environment_proxy=False,
    )

    assert result == "sdk"
    assert captured["http"] == {"trust_env": False, "timeout": 30}
    assert captured["openai"]["max_retries"] == 0


def test_check_server_raises_on_http_error(monkeypatch) -> None:
    import urllib.error

    def fake_open(request, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", {}, None)

    _patch_server_open(monkeypatch, fake_open)

    try:
        _check_server_reachable("http://127.0.0.1:30000/v1")
    except NetworkDoctorError as exc:
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("expected NetworkDoctorError for HTTP 500")


def test_check_server_raises_when_connection_refused(monkeypatch) -> None:
    import urllib.error

    def fake_open(request, timeout):  # noqa: ARG001
        raise urllib.error.URLError(ConnectionRefusedError("Connection refused"))

    _patch_server_open(monkeypatch, fake_open)

    try:
        _check_server_reachable("http://127.0.0.1:9999/v1")
    except NetworkDoctorError as exc:
        assert "could not reach model server" in str(exc)
    else:
        raise AssertionError("expected NetworkDoctorError for unreachable server")


def test_check_server_converts_direct_timeout_to_structured_error(monkeypatch) -> None:
    def time_out(request, timeout):  # noqa: ARG001
        raise TimeoutError("timed out")

    _patch_server_open(monkeypatch, time_out)

    with pytest.raises(NetworkDoctorError, match="timed out after 2.5s"):
        _check_server_reachable("http://127.0.0.1:30000/v1", use_environment_proxy=False)


def test_check_server_uses_direct_proxy_handler_when_requested(monkeypatch) -> None:
    captured = {}

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return types.SimpleNamespace(
            open=lambda request, timeout: _fake_response(),  # noqa: ARG005
        )

    monkeypatch.setattr(
        "minicpm_network_doctor.cli.urllib.request.build_opener",
        build_opener,
    )

    result = _check_server_reachable(
        "http://localhost:11434/v1",
        use_environment_proxy=False,
    )

    proxy_handler = captured["handlers"][0]
    assert proxy_handler.proxies == {}
    assert result["proxy_mode"] == "direct"


def test_check_server_rejects_oversized_response(monkeypatch) -> None:
    _patch_server_open(
        monkeypatch,
        lambda request, timeout: _fake_response(body=b"x" * 1_000_001),  # noqa: ARG005
    )

    with pytest.raises(NetworkDoctorError, match="exceeded 1 MB"):
        _check_server_reachable("http://localhost:30000/v1")


def test_check_server_tolerates_non_json_model_list(monkeypatch) -> None:
    _patch_server_open(
        monkeypatch,
        lambda request, timeout: _fake_response(body=b"not json"),  # noqa: ARG005
    )

    assert _check_server_reachable("http://localhost:30000/v1")["models"] == []


def test_check_server_cli_success_and_json_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "minicpm_network_doctor.cli._check_server_reachable",
        lambda *args, **kwargs: {
            "ok": True,
            "base_url": "http://localhost/v1",
            "models": ["minicpm"],
            "proxy_mode": "direct",
        },
    )
    assert main(["--check-server"]) == 0
    assert "Models: minicpm" in capsys.readouterr().out

    def fail(*args, **kwargs):
        raise NetworkDoctorError("offline")

    monkeypatch.setattr("minicpm_network_doctor.cli._check_server_reachable", fail)
    assert main(["--check-server", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "status": "error",
        "error": "offline",
    }


def test_read_prompt_from_parts_and_stdin(monkeypatch) -> None:
    parser = build_parser()
    assert _read_prompt(["check", "example.com"], parser) == "check example.com"

    fake_stdin = types.SimpleNamespace(isatty=lambda: False, read=lambda: " example.com \n")
    monkeypatch.setattr("minicpm_network_doctor.cli.sys.stdin", fake_stdin)
    assert _read_prompt([], parser) == "example.com"


def test_main_json_success_without_preflight(monkeypatch, capsys) -> None:
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

    class FakeDoctor:
        def __init__(self, client, **kwargs):  # noqa: ARG002
            captured["doctor"] = kwargs

        def diagnose(self, prompt):
            captured["prompt"] = prompt
            return DiagnosisResult(
                text="Done.",
                model_turns=2,
                tool_events=(ToolEvent("resolve_dns", {}, {"ok": True}),),
                targets=("example.com",),
            )

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("minicpm_network_doctor.cli.NetworkDoctor", FakeDoctor)

    code = main(["--skip-server-check", "--json", "--thinking", "example.com"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["status"] == "complete"
    assert captured["prompt"] == "example.com"
    assert captured["doctor"]["thinking"] is True


def test_main_reports_runtime_error(monkeypatch, capsys) -> None:
    class FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

    class FakeDoctor:
        def __init__(self, client, **kwargs):  # noqa: ARG002
            pass

        def diagnose(self, prompt):  # noqa: ARG002
            raise NetworkDoctorError("model failed")

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("minicpm_network_doctor.cli.NetworkDoctor", FakeDoctor)

    assert main(["--skip-server-check", "example.com"]) == 1
    assert "model failed" in capsys.readouterr().err

    assert main(["--skip-server-check", "--json", "example.com"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_print_progress_formats_success_and_failure(capsys) -> None:
    _print_progress(
        ToolEvent("resolve_dns", {}, {"ok": True, "host": "example.com", "duration_ms": 1.2})
    )
    _print_progress(ToolEvent("test_http", {}, {"ok": False, "error": "timeout"}))

    output = capsys.readouterr().err
    assert "[resolve_dns] example.com: ok, 1.2 ms" in output
    assert "[test_http] local context: failed: timeout" in output


@pytest.mark.parametrize(
    "args",
    [
        ["--max-steps", "0", "example.com"],
        ["--max-tool-calls", "25", "example.com"],
        ["--timeout", "0", "example.com"],
        ["--max-retries", "6", "example.com"],
    ],
)
def test_cli_rejects_out_of_range_limits(args) -> None:
    with pytest.raises(SystemExit):
        main(args)
