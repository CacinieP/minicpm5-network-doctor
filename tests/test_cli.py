from minicpm5_network_doctor.agent import NetworkDoctorError
from minicpm5_network_doctor.cli import _check_server_reachable, main


def test_list_tools_does_not_require_model_server(capsys) -> None:
    assert main(["--list-tools"]) == 0
    output = capsys.readouterr().out
    assert "resolve_dns" in output
    assert "inspect_tls" in output
    assert "run_shell" not in output


def _fake_response(status: int = 200):
    import contextlib
    import types

    ctx = types.SimpleNamespace(status=status)
    return contextlib.nullcontext(ctx)


def test_check_server_passes_when_reachable(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _fake_response(200)

    monkeypatch.setattr("minicpm5_network_doctor.cli.urllib.request.urlopen", fake_urlopen)

    # Should not raise.
    _check_server_reachable("http://127.0.0.1:30000/v1")

    assert captured["url"] == "http://127.0.0.1:30000/v1/models"
    # Short probe timeout, not the full --timeout.
    assert captured["timeout"] <= 5


def test_check_server_raises_on_http_error(monkeypatch) -> None:
    import urllib.error

    def fake_urlopen(request, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr("minicpm5_network_doctor.cli.urllib.request.urlopen", fake_urlopen)

    try:
        _check_server_reachable("http://127.0.0.1:30000/v1")
    except NetworkDoctorError as exc:
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("expected NetworkDoctorError for HTTP 500")


def test_check_server_raises_when_connection_refused(monkeypatch) -> None:
    import urllib.error

    def fake_urlopen(request, timeout):  # noqa: ARG001
        raise urllib.error.URLError(ConnectionRefusedError("Connection refused"))

    monkeypatch.setattr("minicpm5_network_doctor.cli.urllib.request.urlopen", fake_urlopen)

    try:
        _check_server_reachable("http://127.0.0.1:99999/v1")
    except NetworkDoctorError as exc:
        assert "could not reach model server" in str(exc)
    else:
        raise AssertionError("expected NetworkDoctorError for unreachable server")
