from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .prompt import load_system_prompt
from .tools import execute_tool, openai_tools, tool_result_json


class NetworkDoctorError(RuntimeError):
    """Base error for the agent runtime."""


class StepLimitError(NetworkDoctorError):
    """Raised when the model does not finish within the configured turn limit."""


@dataclass(frozen=True)
class ToolEvent:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class DiagnosisResult:
    text: str
    model_turns: int
    tool_events: tuple[ToolEvent, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model_turns": self.model_turns,
            "tool_events": [asdict(event) for event in self.tool_events],
        }


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("tool arguments must be JSON")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must decode to an object")
    return parsed


def _assistant_message(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": getattr(message, "content", None),
    }
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in calls
        ]
    return payload


class NetworkDoctor:
    def __init__(
        self,
        client: Any,
        *,
        model: str = "openbmb/MiniCPM5-1B",
        max_steps: int = 6,
        thinking: bool = False,
        system_prompt: str | None = None,
    ) -> None:
        if not 1 <= max_steps <= 12:
            raise ValueError("max_steps must be between 1 and 12")
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.thinking = thinking
        self.system_prompt = system_prompt or load_system_prompt()

    def diagnose(self, query: str) -> DiagnosisResult:
        if not query or not query.strip():
            raise ValueError("query must not be empty")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": query.strip()},
        ]
        events: list[ToolEvent] = []
        seen_calls: set[tuple[str, str]] = set()

        for turn in range(1, self.max_steps + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=openai_tools(),
                    tool_choice="auto",
                    # Diagnostic reasoning is not a creative task: lower sampling
                    # temperatures keep the small model focused on the reported symptom
                    # instead of drifting toward unrelated hosts.
                    temperature=0.6 if self.thinking else 0.4,
                    top_p=0.95,
                    max_tokens=1024,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": self.thinking},
                    },
                )
            except Exception as exc:
                raise NetworkDoctorError(f"MiniCPM5 request failed: {exc}") from exc

            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                content = (getattr(message, "content", None) or "").strip()
                if not content:
                    raise NetworkDoctorError("MiniCPM5 returned neither text nor tool calls")
                return DiagnosisResult(
                    text=content,
                    model_turns=turn,
                    tool_events=tuple(events),
                )

            messages.append(_assistant_message(message))
            for call in calls:
                name = call.function.name
                try:
                    arguments = _parse_arguments(call.function.arguments)
                    signature = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
                    if signature in seen_calls:
                        result = {
                            "ok": False,
                            "error": "duplicate_tool_call",
                            "tool": name,
                        }
                    else:
                        seen_calls.add(signature)
                        result = execute_tool(name, arguments)
                except (ValueError, json.JSONDecodeError) as exc:
                    arguments = {}
                    result = {
                        "ok": False,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "tool": name,
                    }

                events.append(ToolEvent(name=name, arguments=arguments, result=result))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_result_json(result),
                    }
                )

        raise StepLimitError(
            f"MiniCPM5 did not finish after {self.max_steps} model turns; "
            "retry with a more specific symptom"
        )
