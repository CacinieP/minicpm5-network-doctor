from minicpm_network_doctor.agent import NetworkDoctorError
from minicpm_network_doctor.cli import _check_server_reachable, main


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

    monkeypatch.setattr("minicpm_network_doctor.cli.urllib.request.urlopen", fake_urlopen)

    # Should not raise.
    _check_server_reachable("http://127.0.0.1:30000/v1")

    assert captured["url"] == "http://127.0.0.1:30000/v1/models"
    # Short probe timeout, not the full --timeout.
    assert captured["timeout"] <= 5


def test_check_server_raises_on_http_error(monkeypatch) -> None:
    import urllib.error

    def fake_urlopen(request, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr("minicpm_network_doctor.cli.urllib.request.urlopen", fake_urlopen)

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

    monkeypatch.setattr("minicpm_network_doctor.cli.urllib.request.urlopen", fake_urlopen)

    try:
        _check_server_reachable("http://127.0.0.1:99999/v1")
    except NetworkDoctorError as exc:
        assert "could not reach model server" in str(exc)
    else:
        raise AssertionError("expected NetworkDoctorError for unreachable server")


class _Drop(Exception):
    pass


def _tool_call(name, arguments, call_id="call-1"):
    import json
    import types

    return types.SimpleNamespace(
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _response(*, content=None, tool_calls=None):
    import types

    message = types.SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _fake_openai_module(responses, failures_at=()):
    """Build a fake `openai` module whose OpenAI client scripts responses.

    cli.main imports `from openai import OpenAI` lazily, so injecting a module
    into sys.modules is enough to drive the full CLI path without a server.
    """
    import types

    state = {"calls": 0}

    class _Completions:
        def create(self, **kwargs):
            state["calls"] += 1
            if state["calls"] in failures_at:
                raise _Drop("backend down")
            return responses.pop(0)

    class _OpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = types.SimpleNamespace(completions=_Completions())

    module = types.ModuleType("openai")
    module.OpenAI = _OpenAI
    return module


def _passing_probe(monkeypatch):
    import contextlib
    import types

    def fake_urlopen(request, timeout):
        return contextlib.nullcontext(types.SimpleNamespace(status=200))

    monkeypatch.setattr("minicpm_network_doctor.cli.urllib.request.urlopen", fake_urlopen)


def test_main_runs_diagnosis_and_writes_rollout(tmp_path, monkeypatch, capsys) -> None:
    import sys

    responses = [
        _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})]),
        _response(content="Diagnosis: DNS is fine."),
    ]
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(list(responses)))
    _passing_probe(monkeypatch)
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "host": arguments.get("host")},
    )

    from minicpm_network_doctor.cli import main

    code = main(["--rollout-dir", str(tmp_path), "check example.com"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Diagnosis: DNS is fine." in out
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1


def test_main_json_mode_exposes_trace_and_rollout_path(tmp_path, monkeypatch, capsys) -> None:
    import json
    import sys

    responses = [
        _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})]),
        _response(content="Diagnosis: DNS is fine."),
    ]
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(list(responses)))
    _passing_probe(monkeypatch)
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "host": arguments.get("host")},
    )

    from minicpm_network_doctor.cli import main

    code = main(["--json", "--rollout-dir", str(tmp_path), "check example.com"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "Diagnosis: DNS is fine."
    assert payload["model_turns"] == 2
    assert payload["tool_events"][0]["name"] == "resolve_dns"
    assert payload["rollout_path"].endswith(".jsonl")


def test_main_no_rollout_flag_skips_logging(tmp_path, monkeypatch, capsys) -> None:
    import sys

    responses = [
        _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})]),
        _response(content="Diagnosis: DNS is fine."),
    ]
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(list(responses)))
    _passing_probe(monkeypatch)
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "host": arguments.get("host")},
    )

    from minicpm_network_doctor.cli import main

    code = main(["--no-rollout", "--rollout-dir", str(tmp_path), "check example.com"])

    assert code == 0
    assert list(tmp_path.iterdir()) == []


def test_main_returns_1_and_prints_error_when_backend_dies(monkeypatch, capsys) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module([], failures_at=(1,)))
    _passing_probe(monkeypatch)

    from minicpm_network_doctor.cli import main

    code = main(["check example.com"])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "MiniCPM5 request failed" in captured.err
