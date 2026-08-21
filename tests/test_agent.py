from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minicpm_network_doctor.agent import (
    NetworkDoctor,
    NetworkDoctorError,
    ToolEvent,
    _build_partial_text,
    _is_unsupported_tool_choice,
)


class BadRequestError(Exception):
    """Minimal stand-in for openai.BadRequestError, avoiding a real openai import."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _response(*, content: str | None = None, tool_calls=None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return next(self.responses)


def _client(responses: list[SimpleNamespace]) -> SimpleNamespace:
    completions = FakeCompletions(responses)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        fake_completions=completions,
    )


def test_agent_executes_tool_then_returns_answer(monkeypatch) -> None:
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})]),
            _response(content="Diagnosis: DNS works."),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "tool": name, "host": arguments["host"]},
    )

    result = NetworkDoctor(fake, system_prompt="test prompt").diagnose("Check example.com")

    assert result.text == "Diagnosis: DNS works."
    assert result.model_turns == 2
    assert result.tool_events[0].name == "resolve_dns"
    second_request = fake.fake_completions.requests[1]
    assert second_request["messages"][-1]["role"] == "tool"
    assert second_request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_duplicate_tool_call_is_not_executed_twice(monkeypatch) -> None:
    call = _tool_call("resolve_dns", {"host": "example.com"})
    fake = _client(
        [
            _response(tool_calls=[call]),
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"}, "call-2")]),
            _response(content="Done."),
        ]
    )
    executed: list[str] = []

    def execute(name, arguments):
        executed.append(name)
        return {"ok": True}

    monkeypatch.setattr("minicpm_network_doctor.agent.execute_tool", execute)
    result = NetworkDoctor(fake, max_steps=3, system_prompt="test prompt").diagnose("Check DNS")

    assert executed == ["resolve_dns"]
    assert result.tool_events[1].result["error"] == "duplicate_tool_call"


def test_repeated_same_tool_returns_partial_diagnosis(monkeypatch) -> None:
    """When the model calls the same tool 3 turns in a row, stop and return a
    structured partial diagnosis instead of looping until max_steps."""
    # Each turn returns a resolve_dns call; different hosts so they are not
    # deduplicated, but the repeated tool name triggers the streak detector.
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "a.com"}, "c1")]),
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "b.com"}, "c2")]),
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "c.com"}, "c3")]),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "host": arguments.get("host")},
    )

    result = NetworkDoctor(fake, max_steps=6, system_prompt="test prompt").diagnose("Check")

    assert result.model_turns == 3
    assert len(result.tool_events) == 3
    assert "did not converge" in result.text
    assert "resolve_dns" in result.text


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError):
        NetworkDoctor(_client([]), system_prompt="test prompt").diagnose(" ")


def test_first_turn_forces_tool_choice_required(monkeypatch) -> None:
    """Turn 1 must request tool_choice='required'; later turns use 'auto'."""
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "x.com"})]),
            _response(content="Done."),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True},
    )

    NetworkDoctor(fake, system_prompt="test prompt").diagnose("Check x.com")

    requests = fake.fake_completions.requests
    assert requests[0]["tool_choice"] == "required"
    assert requests[1]["tool_choice"] == "auto"


def test_required_falls_back_to_auto_when_unsupported(monkeypatch) -> None:
    """If the backend rejects tool_choice='required', retry the first turn with 'auto'."""
    requests: list[dict] = []

    class FailingThenOk:
        def create(self, **kwargs):
            requests.append(kwargs)
            if kwargs.get("tool_choice") == "required":
                raise BadRequestError("tool_choice 'required' is not supported")
            return _response(tool_calls=[_tool_call("resolve_dns", {"host": "y.com"})])

    class ThenAnswer:
        def create(self, **kwargs):
            requests.append(kwargs)
            return _response(content="Done.")

    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True},
    )

    # First turn: required fails -> fallback to auto -> tool call.
    # Second turn: auto -> answer.
    sequence = iter([FailingThenOk(), ThenAnswer()])

    def create(**kwargs):
        return next(sequence).create(**kwargs)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = NetworkDoctor(fake, system_prompt="test prompt").diagnose("Check y.com")

    choices = [r["tool_choice"] for r in requests]
    assert choices[0] == "required"  # first attempt
    assert "auto" in choices  # fallback appeared
    assert result.text == "Done."


def test_is_unsupported_tool_choice_detects_400() -> None:
    assert _is_unsupported_tool_choice(BadRequestError("tool_choice invalid")) is True


def test_is_unsupported_tool_choice_ignores_other_errors() -> None:
    assert _is_unsupported_tool_choice(RuntimeError("connection reset")) is False


def test_build_partial_text_has_four_sections() -> None:
    events = (
        ToolEvent("resolve_dns", {"host": "a.com"}, {"ok": True, "host": "a.com", "addresses": []}),
        ToolEvent("test_tcp", {"host": "a.com", "port": 443}, {"ok": False, "error": "refused"}),
    )
    text = _build_partial_text(events, reason="test reason")
    assert "Diagnosis:" in text
    assert "Evidence:" in text
    assert "Recommended action:" in text
    assert "Verification:" in text
    assert "resolve_dns" in text
    assert "test_tcp" in text
    assert "refused" in text


class ConnectionDrop(Exception):
    """Stand-in for a transport failure from a local inference server."""


class FlakyCompletions:
    """Yields scripted responses, raising ConnectionDrop at scripted points."""

    def __init__(self, responses: list, failures_at: list[int]) -> None:
        self.responses = iter(responses)
        self.failures_at = failures_at
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls in self.failures_at:
            raise ConnectionDrop("connection refused by backend")
        return next(self.responses)


def _read_rollout(path: str) -> list[dict]:
    assert path, "expected a rollout path"
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_backend_failure_mid_run_returns_partial_diagnosis(tmp_path, monkeypatch) -> None:
    """A backend hiccup after evidence exists must not discard that evidence:
    retry once, then exit through the partial-diagnosis path."""
    flaky = FlakyCompletions(
        [_response(tool_calls=[_tool_call("resolve_dns", {"host": "a.com"})])],
        failures_at=[2, 3],
    )
    fake = SimpleNamespace(chat=SimpleNamespace(completions=flaky))
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "host": "a.com"},
    )
    doctor = NetworkDoctor(
        fake,
        system_prompt="test prompt",
        rollout_dir=tmp_path,
        backend_retry_delay=0.0,
    )
    result = doctor.diagnose("Check a.com")

    assert "backend became unavailable" in result.text
    assert "resolve_dns" in result.text
    assert result.model_turns == 1
    records = _read_rollout(result.rollout_path)
    assert records[-1]["exit_status"] == "backend_error"
    assert records[0]["event"] == "query"


def test_backend_failure_before_any_evidence_raises(tmp_path) -> None:
    flaky = FlakyCompletions([], failures_at=[1])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=flaky))
    doctor = NetworkDoctor(
        fake,
        system_prompt="test prompt",
        rollout_dir=tmp_path,
        backend_retry_delay=0.0,
    )
    with pytest.raises(NetworkDoctorError):
        doctor.diagnose("Check a.com")

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    records = _read_rollout(str(files[0]))
    assert records[-1]["exit_status"] == "backend_error"


def test_model_error_streak_returns_partial_diagnosis(tmp_path, monkeypatch) -> None:
    """Three consecutive malformed tool usages stop the run, unlike network
    failures, which are evidence."""
    fake = _client(
        [
            _response(tool_calls=[_tool_call("bogus_a", {"host": "a.com"}, "c1")]),
            _response(tool_calls=[_tool_call("bogus_b", {"host": "b.com"}, "c2")]),
            _response(tool_calls=[_tool_call("bogus_c", {"host": "c.com"}, "c3")]),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": False, "error": "unknown_tool", "tool": name},
    )
    result = NetworkDoctor(
        fake,
        max_steps=6,
        system_prompt="test prompt",
        rollout_dir=tmp_path,
    ).diagnose("Check")

    assert "malformed tool calls" in result.text
    records = _read_rollout(result.rollout_path)
    assert records[-1]["exit_status"] == "model_error_limit"
    assert [r["error_class"] for r in records if r["event"] == "tool_result"] == [
        "model_error",
        "model_error",
        "model_error",
    ]


def test_network_failures_do_not_trip_error_breaker(tmp_path, monkeypatch) -> None:
    """Failed DNS/TCP/HTTP checks are the point of a diagnosis: they must not
    count as model errors, so the run continues to the step limit."""
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "a.com"}, "c1")]),
            _response(tool_calls=[_tool_call("test_tcp", {"host": "a.com", "port": 443}, "c2")]),
            _response(tool_calls=[_tool_call("test_http", {"url": "https://a.com"}, "c3")]),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {
            "ok": False,
            "error": "Name or service not known",
            "error_type": "gaierror",
            "tool": name,
        },
    )
    result = NetworkDoctor(
        fake,
        max_steps=3,
        system_prompt="test prompt",
        rollout_dir=tmp_path,
    ).diagnose("Check a.com")

    assert "did not converge within 3 turns" in result.text
    assert "malformed" not in result.text
    records = _read_rollout(result.rollout_path)
    assert records[-1]["exit_status"] == "step_limit"
    tool_records = [r for r in records if r["event"] == "tool_result"]
    assert [r["error_class"] for r in tool_records] == [
        "tool_finding",
        "tool_finding",
        "tool_finding",
    ]


def test_rollout_written_on_completion(tmp_path, monkeypatch) -> None:
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "x.com"})]),
            _response(content="Diagnosis: DNS works."),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "tool": name},
    )
    result = NetworkDoctor(
        fake,
        system_prompt="test prompt",
        rollout_dir=tmp_path,
    ).diagnose("Check x.com")

    records = _read_rollout(result.rollout_path)
    assert records[0]["event"] == "query"
    assert records[0]["query"] == "Check x.com"
    assert any(r["event"] == "tool_result" and r["ok"] for r in records)
    assert records[-1]["event"] == "exit"
    assert records[-1]["exit_status"] == "completed"
    assert records[-1]["model_turns"] == 2


def test_rollout_disabled_leaves_no_file(tmp_path, monkeypatch) -> None:
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "x.com"})]),
            _response(content="Diagnosis: DNS works."),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True, "tool": name},
    )
    result = NetworkDoctor(
        fake,
        system_prompt="test prompt",
        rollout=False,
        rollout_dir=tmp_path,
    ).diagnose("Check x.com")

    assert result.rollout_path == ""
    assert list(tmp_path.iterdir()) == []


def test_rollout_written_when_response_is_empty(tmp_path, monkeypatch) -> None:
    """A run that dies mid-diagnosis still leaves its rollout behind."""
    fake = _client([_response(content=None)])
    doctor = NetworkDoctor(
        fake,
        system_prompt="test prompt",
        rollout_dir=tmp_path,
    )
    with pytest.raises(NetworkDoctorError):
        doctor.diagnose("Check x.com")

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    records = _read_rollout(str(files[0]))
    assert records[-1]["exit_status"] == "empty_response"
