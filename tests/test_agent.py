from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minicpm_network_doctor.agent import (
    NetworkDoctor,
    NetworkDoctorError,
    StepLimitError,
    ToolEvent,
    _build_partial_text,
    _is_unsupported_reasoning_effort,
    _is_unsupported_tool_choice,
    _response_message,
)
from minicpm_network_doctor.prompt import load_system_prompt


class BadRequestError(Exception):
    """Minimal stand-in for openai.BadRequestError, avoiding a real openai import."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_system_prompt_requires_corroboration_for_fake_ip() -> None:
    prompt = load_system_prompt().lower()

    assert "path/topology evidence only" in prompt
    assert "does not by itself prove" in prompt
    assert "controlled direct/bypass" in prompt
    assert "strong evidence of dns hijacking" not in prompt


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
        lambda name, arguments, **kwargs: {
            "ok": True,
            "tool": name,
            "host": arguments["host"],
        },
    )

    result = NetworkDoctor(fake, system_prompt="test prompt").diagnose("Check example.com")

    assert result.text == "Diagnosis: DNS works."
    assert result.model_turns == 2
    assert result.tool_events[0].name == "resolve_dns"
    second_request = fake.fake_completions.requests[1]
    assert second_request["messages"][-1]["role"] == "tool"
    assert second_request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert second_request["reasoning_effort"] == "none"


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

    def execute(name, arguments, **kwargs):  # noqa: ARG001
        executed.append(name)
        return {"ok": True}

    monkeypatch.setattr("minicpm_network_doctor.agent.execute_tool", execute)
    result = NetworkDoctor(fake, max_steps=3, system_prompt="test prompt").diagnose(
        "Check example.com DNS"
    )

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
        lambda name, arguments, **kwargs: {"ok": True, "host": arguments.get("host")},
    )

    result = NetworkDoctor(fake, max_steps=6, system_prompt="test prompt").diagnose(
        "Check a.com, b.com, and c.com"
    )

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
        lambda name, arguments, **kwargs: {"ok": True},
    )

    NetworkDoctor(fake, system_prompt="test prompt").diagnose("Check x.com")

    requests = fake.fake_completions.requests
    assert requests[0]["tool_choice"] == "required"
    assert requests[1]["tool_choice"] == "auto"


def test_required_falls_back_to_auto_when_unsupported(monkeypatch) -> None:
    """If the backend rejects tool_choice='required', retry the first turn with 'auto'."""
    requests: list[dict] = []

    class FailingThenOk:
        def __init__(self) -> None:
            self.failed = False

        def create(self, **kwargs):
            requests.append(kwargs)
            if kwargs.get("tool_choice") == "required" and not self.failed:
                self.failed = True
                raise BadRequestError("tool_choice 'required' is not supported")
            return _response(tool_calls=[_tool_call("resolve_dns", {"host": "y.com"})])

    class ThenAnswer:
        def create(self, **kwargs):
            requests.append(kwargs)
            return _response(content="Done.")

    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments, **kwargs: {"ok": True},
    )

    # First turn: required fails -> fallback to auto -> tool call.
    # Second turn: auto -> answer.
    first = FailingThenOk()
    second = ThenAnswer()
    calls = 0

    def create(**kwargs):
        nonlocal calls
        calls += 1
        return first.create(**kwargs) if calls <= 2 else second.create(**kwargs)

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
    assert _is_unsupported_tool_choice(BadRequestError("invalid model name")) is False


def test_reasoning_effort_falls_back_only_for_specific_backend_error(monkeypatch) -> None:
    requests = []

    class Completions:
        def create(self, **kwargs):
            requests.append(kwargs)
            if "reasoning_effort" in kwargs:
                raise BadRequestError("reasoning_effort is unsupported")
            if len(requests) == 2:
                return _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})])
            return _response(content="Done.")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments, **kwargs: {"ok": True, "host": arguments["host"]},
    )

    result = NetworkDoctor(fake, system_prompt="test prompt").diagnose("example.com")

    assert result.text == "Done."
    assert "reasoning_effort" in requests[0]
    assert "reasoning_effort" not in requests[1]
    assert _is_unsupported_reasoning_effort(BadRequestError("invalid model")) is False


def test_response_message_rejects_empty_or_malformed_choices() -> None:
    with pytest.raises(NetworkDoctorError, match="no completion choices"):
        _response_message(SimpleNamespace(choices=[]))
    with pytest.raises(NetworkDoctorError, match="without a message"):
        _response_message(SimpleNamespace(choices=[SimpleNamespace()]))


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


def test_plain_answer_without_evidence_is_rejected_then_retried(monkeypatch) -> None:
    fake = _client(
        [
            _response(content="It is probably DNS."),
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})]),
            _response(
                content=(
                    "Diagnosis: DNS works.\n\nEvidence: public IP.\n\n"
                    "Recommended action: no change.\n\nVerification: dig example.com"
                )
            ),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments, **kwargs: {"ok": True, "host": arguments["host"]},
    )

    result = NetworkDoctor(fake, system_prompt="test prompt").diagnose("Check example.com")

    assert result.model_turns == 3
    assert result.warnings == ("backend_ignored_required_tool_choice",)
    assert [request["tool_choice"] for request in fake.fake_completions.requests] == [
        "required",
        "required",
        "auto",
    ]


def test_repeated_plain_answers_without_evidence_raise_step_limit() -> None:
    fake = _client([_response(content="Guess one"), _response(content="Guess two")])

    with pytest.raises(StepLimitError, match="no usable tool evidence"):
        NetworkDoctor(fake, max_steps=2, system_prompt="test prompt").diagnose("Check example.com")


def test_tool_call_budget_and_progress_callback(monkeypatch) -> None:
    calls = [
        _tool_call("resolve_dns", {"host": "example.com", "port": 440 + index}, f"c{index}")
        for index in range(5)
    ]
    fake = _client([_response(tool_calls=calls), _response(content="Done.")])
    executed = []
    observed = []

    def execute(name, arguments, **kwargs):
        executed.append(arguments["port"])
        return {"ok": True, "host": arguments["host"]}

    monkeypatch.setattr("minicpm_network_doctor.agent.execute_tool", execute)
    result = NetworkDoctor(
        fake,
        system_prompt="test prompt",
        on_tool_event=observed.append,
    ).diagnose("Check example.com")

    assert executed == [440, 441, 442, 443]
    assert len(observed) == 5
    assert result.tool_events[-1].result["error"] == "tool_call_limit_exceeded"
    assert "model_answer_missing_required_sections" in result.warnings


def test_query_requires_explicit_target_and_has_size_limit() -> None:
    doctor = NetworkDoctor(_client([]), system_prompt="test prompt")

    with pytest.raises(ValueError, match="must include"):
        doctor.diagnose("npm install times out")
    with pytest.raises(ValueError, match="8000"):
        doctor.diagnose("example.com " + "x" * 8_000)


def test_result_json_has_stable_envelope(monkeypatch) -> None:
    fake = _client(
        [
            _response(tool_calls=[_tool_call("resolve_dns", {"host": "example.com"})]),
            _response(content="Done."),
        ]
    )
    monkeypatch.setattr(
        "minicpm_network_doctor.agent.execute_tool",
        lambda name, arguments, **kwargs: {"ok": True, "host": arguments["host"]},
    )

    payload = NetworkDoctor(fake, system_prompt="test prompt").diagnose("example.com").as_dict()

    assert payload["schema_version"] == 1
    assert payload["status"] == "complete"
    assert payload["targets"] == ["example.com"]
    assert payload["warnings"] == ["model_answer_missing_required_sections"]
