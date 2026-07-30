from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minicpm5_network_doctor.agent import NetworkDoctor, StepLimitError


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


def test_step_limit_is_enforced(monkeypatch) -> None:
    fake = _client([_response(tool_calls=[_tool_call("resolve_dns", {"host": "localhost"})])])
    monkeypatch.setattr(
        "minicpm5_network_doctor.agent.execute_tool",
        lambda name, arguments: {"ok": True},
    )

    with pytest.raises(StepLimitError):
        NetworkDoctor(fake, max_steps=1, system_prompt="test prompt").diagnose("Check localhost")


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError):
        NetworkDoctor(_client([]), system_prompt="test prompt").diagnose(" ")
