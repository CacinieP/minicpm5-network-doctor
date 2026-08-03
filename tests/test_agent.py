from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minicpm5_network_doctor.agent import (
    NetworkDoctor,
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
        "minicpm5_network_doctor.agent.execute_tool",
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

    monkeypatch.setattr("minicpm5_network_doctor.agent.execute_tool", execute)
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
        "minicpm5_network_doctor.agent.execute_tool",
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
        "minicpm5_network_doctor.agent.execute_tool",
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
        "minicpm5_network_doctor.agent.execute_tool",
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
