from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from .prompt import load_system_prompt
from .scope import extract_targets
from .tools import execute_tool, openai_tools, tool_result_json

_REQUIRED_SECTIONS = ("Diagnosis:", "Evidence:", "Recommended action:", "Verification:")
_NON_EVIDENCE_ERRORS = {
    "duplicate_tool_call",
    "target_missing_or_invalid",
    "target_out_of_scope",
    "tool_call_limit_exceeded",
    "unknown_tool",
}


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
    status: str = "complete"
    targets: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "text": self.text,
            "model_turns": self.model_turns,
            "targets": list(self.targets),
            "warnings": list(self.warnings),
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


def _is_unsupported_tool_choice(exc: BaseException) -> bool:
    """Heuristically detect that a backend rejected ``tool_choice="required"``.

    Backends vary in how they signal this: OpenAI returns a 400 with a message
    mentioning the parameter; local servers may raise a generic ``BadRequestError``.
    We require a parameter-specific marker. Retrying every generic HTTP 400 can
    duplicate unrelated bad requests and hide the real server error.
    """
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    markers = ("tool_choice", "required tool", "forced tool", "unsupported choice")
    return status in {None, 400, 422} and any(marker in text for marker in markers)


def _is_unsupported_reasoning_effort(exc: BaseException) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    return status in {None, 400, 422} and any(
        marker in text for marker in ("reasoning_effort", "reasoning effort")
    )


def _response_message(response: Any) -> Any:
    choices = getattr(response, "choices", None)
    if not choices:
        raise NetworkDoctorError("MiniCPM5 returned no completion choices")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise NetworkDoctorError("MiniCPM5 returned a completion without a message")
    return message


def _summarise_event(event: ToolEvent) -> str:
    """One-line, model-readable summary of a single tool event's outcome."""
    result = event.result
    host = result.get("host") or result.get("url", "")
    target = f" ({host})" if host else ""
    if not result.get("ok"):
        error = result.get("error", "unknown error")
        return f"{event.name}{target} -> failed: {error}"
    addresses = result.get("addresses")
    if isinstance(addresses, list) and addresses:
        first = addresses[0]
        if isinstance(first, dict):
            value = first.get("address", "unknown")
            classification = first.get("classification")
            if classification:
                value = f"{value} ({classification})"
            if len(addresses) > 1:
                value = f"{value}; {len(addresses)} addresses total"
            return f"{event.name}{target} -> addresses={value}"
    # Pick the most informative field for each tool without dumping everything.
    for key in (
        "status",
        "protocol",
        "overridden",
        "proxy_variables",
        "operating_system",
    ):
        if key in result:
            value = result[key]
            if isinstance(value, list) and value:
                value = value[0] if len(value) == 1 else f"{len(value)} entries"
            return f"{event.name}{target} -> {key}={value}"
    return f"{event.name}{target} -> ok"


def _build_partial_text(events: tuple[ToolEvent, ...], *, reason: str, language: str = "en") -> str:
    """Compose a structured partial diagnosis when the model does not converge.

    Follows the four-section layout the system prompt asks the model to use, so the
    user still gets a consistent shape even when the model failed to finish.
    """
    evidence_lines = "\n".join(f"- {_summarise_event(e)}" for e in events)
    if language == "zh":
        return (
            f"Diagnosis: 模型未能收敛到最终诊断（{reason}）。下方保留了已收集的证据，"
            "其中仍可能包含问题线索。\n"
            "\n"
            "Evidence:\n"
            f"{evidence_lines}\n"
            "\n"
            "Recommended action: 使用 --thinking 重试，或在症状中补充准确的主机、端口和"
            "错误原文，以便模型选择更有针对性的检查。\n"
            "\n"
            "Verification: 在诊断收敛前，暂时无法可靠推荐唯一的验证命令。"
        )
    return (
        "Diagnosis: The model did not converge on a final diagnosis "
        f"({reason}). The evidence collected so far is below; it may still "
        "point toward the cause.\n"
        "\n"
        "Evidence:\n"
        f"{evidence_lines}\n"
        "\n"
        "Recommended action: retry with --thinking, or restate the symptom with the "
        "exact host, port, and error text so the model can pick a sharper check.\n"
        "\n"
        "Verification: no single command can be recommended until a diagnosis converges."
    )


class NetworkDoctor:
    def __init__(
        self,
        client: Any,
        *,
        model: str = "openbmb/MiniCPM5-1B",
        max_steps: int = 6,
        max_tool_calls: int = 12,
        thinking: bool = False,
        system_prompt: str | None = None,
        on_tool_event: Callable[[ToolEvent], None] | None = None,
    ) -> None:
        if not 1 <= max_steps <= 12:
            raise ValueError("max_steps must be between 1 and 12")
        if not 1 <= max_tool_calls <= 24:
            raise ValueError("max_tool_calls must be between 1 and 24")
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.thinking = thinking
        self.system_prompt = system_prompt or load_system_prompt()
        self.on_tool_event = on_tool_event

    def _create_with_fallback(self, messages: list[dict[str, Any]], *, tool_choice: str) -> Any:
        """Send a completion with narrow compatibility fallbacks.

        The first turn forces ``"required"`` so the model must call a tool before
        answering. Some backends reject that value (older runtimes, or when the
        template has no forced-call path); in that case we retry the same request
        with ``"auto"``. Backends that reject ``reasoning_effort`` are retried
        without that field while retaining the SGLang chat-template control.
        """
        choice = tool_choice
        include_reasoning_effort = True
        for _ in range(3):
            request: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": openai_tools(),
                "tool_choice": choice,
                # Diagnostic reasoning is not a creative task: lower sampling
                # temperatures keep the small model focused on the reported symptom
                # instead of drifting toward unrelated hosts.
                "temperature": 0.6 if self.thinking else 0.4,
                "top_p": 0.95,
                "max_tokens": 1024,
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": self.thinking},
                },
            }
            if include_reasoning_effort:
                request["reasoning_effort"] = "high" if self.thinking else "none"
            try:
                return self.client.chat.completions.create(**request)
            except Exception as exc:
                if include_reasoning_effort and _is_unsupported_reasoning_effort(exc):
                    include_reasoning_effort = False
                    continue
                if choice == "required" and _is_unsupported_tool_choice(exc):
                    choice = "auto"
                    continue
                raise
        raise NetworkDoctorError("MiniCPM5 request compatibility fallbacks were exhausted")

    def diagnose(self, query: str) -> DiagnosisResult:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if len(query) > 8_000:
            raise ValueError("query must be 8000 characters or fewer")

        cleaned_query = query.strip()
        targets = extract_targets(cleaned_query)
        if not targets:
            raise ValueError("query must include the affected hostname, IP address, or HTTP(S) URL")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": cleaned_query},
        ]
        events: list[ToolEvent] = []
        warnings: list[str] = []
        seen_calls: set[tuple[str, str]] = set()
        executed_calls = 0
        # Track repeated tool names to detect when the model is stuck calling the
        # same tool over and over without converging on an answer.
        last_tool_name: str | None = None
        tool_name_streak = 0
        non_convergence_reason: str | None = None

        for turn in range(1, self.max_steps + 1):
            # Force a tool call on the first turn so the model must gather evidence
            # before speaking. This eliminates the failure mode where the small model
            # answers from priors ("I don't have that tool") instead of checking.
            has_evidence = _has_evidence(events)
            choice = "required" if not has_evidence else "auto"
            try:
                response = self._create_with_fallback(
                    messages,
                    tool_choice=choice,
                )
            except Exception as exc:
                raise NetworkDoctorError(f"MiniCPM5 request failed: {exc}") from exc

            message = _response_message(response)
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                content = (getattr(message, "content", None) or "").strip()
                if not content:
                    raise NetworkDoctorError("MiniCPM5 returned neither text nor tool calls")
                if not has_evidence:
                    warning = "backend_ignored_required_tool_choice"
                    if warning not in warnings:
                        warnings.append(warning)
                    messages.append(_assistant_message(message))
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Do not diagnose yet. First call one read-only tool against only "
                                f"the reported target(s): {', '.join(sorted(targets))}."
                            ),
                        }
                    )
                    continue

                missing_sections = [
                    section for section in _REQUIRED_SECTIONS if section not in content
                ]
                if missing_sections:
                    warnings.append("model_answer_missing_required_sections")
                return DiagnosisResult(
                    text=content,
                    model_turns=turn,
                    tool_events=tuple(events),
                    targets=tuple(sorted(targets)),
                    warnings=tuple(warnings),
                )

            messages.append(_assistant_message(message))
            turn_names: list[str] = []
            for call_index, call in enumerate(calls):
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
                    elif call_index >= 4 or executed_calls >= self.max_tool_calls:
                        result = {
                            "ok": False,
                            "error": "tool_call_limit_exceeded",
                            "tool": name,
                        }
                    else:
                        seen_calls.add(signature)
                        executed_calls += 1
                        result = execute_tool(name, arguments, allowed_hosts=targets)
                except (ValueError, json.JSONDecodeError) as exc:
                    arguments = {}
                    result = {
                        "ok": False,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "tool": name,
                    }

                event = ToolEvent(name=name, arguments=arguments, result=result)
                events.append(event)
                if self.on_tool_event is not None:
                    self.on_tool_event(event)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_result_json(result),
                    }
                )
                turn_names.append(name)

            # Update the same-tool streak across the whole turn. A turn is counted as
            # continuing the streak only when every call in it uses the same name as
            # the previous one, so a healthy mixed sequence never trips this.
            if turn_names and all(n == turn_names[0] for n in turn_names):
                head = turn_names[0]
                if head == last_tool_name:
                    tool_name_streak += 1
                else:
                    last_tool_name = head
                    tool_name_streak = 1
                if tool_name_streak >= 3:
                    non_convergence_reason = (
                        f"the model called {head!r} {tool_name_streak} turns in a row "
                        "without finishing"
                    )
                    break
            else:
                last_tool_name = None
                tool_name_streak = 0

        if not _has_evidence(events):
            raise StepLimitError(
                f"MiniCPM5 produced no usable tool evidence after {self.max_steps} model turns; "
                "verify that the backend supports native tool calls"
            )

        language = "zh" if re.search(r"[\u3400-\u9fff]", cleaned_query) else "en"
        return DiagnosisResult(
            text=_build_partial_text(
                tuple(events),
                reason=non_convergence_reason
                or f"the model did not converge within {self.max_steps} turns",
                language=language,
            ),
            model_turns=turn,
            tool_events=tuple(events),
            status="partial",
            targets=tuple(sorted(targets)),
            warnings=tuple(warnings),
        )


def _has_evidence(events: list[ToolEvent]) -> bool:
    for event in events:
        error = event.result.get("error")
        error_type = event.result.get("error_type")
        if error in _NON_EVIDENCE_ERRORS:
            continue
        if error_type in {"JSONDecodeError", "TypeError", "ValueError"}:
            continue
        return True
    return False
