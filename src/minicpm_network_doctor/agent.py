from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .prompt import load_system_prompt
from .rollout import NullRolloutWriter, RolloutWriter
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
    rollout_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model_turns": self.model_turns,
            "tool_events": [asdict(event) for event in self.tool_events],
            "rollout_path": self.rollout_path,
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
    We match on the status code and on common substrings in the error text.
    """
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status == 400:
        return True
    return any(
        marker in text for marker in ("tool_choice", "tool call", "forced", "unsupported choice")
    )


# Failure markers produced by the runtime itself (before any tool runs) or by
# argument validation: these are the model's mistakes, not network findings.
_MODEL_ERROR_MARKERS = frozenset(
    {"unknown_tool", "arguments_must_be_an_object", "duplicate_tool_call"}
)

# Error types raised by the network stack (socket.gaierror reports as
# "gaierror"); a tool returning one of these is *negative evidence* — exactly
# what a diagnosis of a broken network looks like — so it must never count
# against the model.
_EVIDENCE_ERROR_TYPES = frozenset(
    {
        "OSError",
        "gaierror",
        "timeout",
        "TimeoutError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
    }
)


def _classify_tool_failure(result: dict[str, Any]) -> str:
    """Classify an ``ok=False`` tool result for the error breaker.

    Returns one of ``"model_error"`` (malformed usage — unknown tool, invalid
    arguments, duplicate call), ``"tool_finding"`` (the check ran and reported
    a network failure — diagnostic evidence), or ``"internal"`` (anything
    unexpected, e.g. a bug inside a handler). Only ``model_error`` and
    ``internal`` count toward the consecutive breaker: a productive diagnosis
    of a down host produces a stream of ``tool_finding`` failures that must
    not stop the run.
    """
    if result.get("error") in _MODEL_ERROR_MARKERS:
        return "model_error"
    if result.get("error_type") == "ValueError":
        return "model_error"
    if result.get("error_type") in _EVIDENCE_ERROR_TYPES:
        return "tool_finding"
    return "internal"


def _summarise_event(event: ToolEvent) -> str:
    """One-line, model-readable summary of a single tool event's outcome."""
    result = event.result
    host = result.get("host") or result.get("url", "")
    target = f" ({host})" if host else ""
    if not result.get("ok"):
        error = result.get("error", "unknown error")
        return f"{event.name}{target} -> failed: {error}"
    # Pick the most informative field for each tool without dumping everything.
    for key in ("status", "addresses", "protocol", "proxy_variables", "operating_system"):
        if key in result:
            value = result[key]
            if isinstance(value, list) and value:
                value = value[0] if len(value) == 1 else f"{len(value)} entries"
            return f"{event.name}{target} -> {key}={value}"
    return f"{event.name}{target} -> ok"


def _build_partial_text(events: tuple[ToolEvent, ...], *, reason: str) -> str:
    """Compose a structured partial diagnosis when the model does not converge.

    Follows the four-section layout the system prompt asks the model to use, so the
    user still gets a consistent shape even when the model failed to finish.
    """
    evidence_lines = "\n".join(f"- {_summarise_event(e)}" for e in events)
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
        thinking: bool = False,
        system_prompt: str | None = None,
        rollout: bool = True,
        rollout_dir: Path | None = None,
        backend_retry_delay: float = 1.0,
    ) -> None:
        if not 1 <= max_steps <= 12:
            raise ValueError("max_steps must be between 1 and 12")
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.thinking = thinking
        self.system_prompt = system_prompt or load_system_prompt()
        self.rollout = rollout
        self.rollout_dir = rollout_dir
        self.backend_retry_delay = backend_retry_delay

    def _create_with_fallback(self, messages: list[dict[str, Any]], *, tool_choice: str) -> Any:
        """Send a chat completion request, falling back to ``tool_choice="auto"``.

        The first turn forces ``"required"`` so the model must call a tool before
        answering. Some backends reject that value (older runtimes, or when the
        template has no forced-call path); in that case we retry the same request
        once with ``"auto"`` rather than aborting the whole diagnosis.
        """
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=openai_tools(),
                tool_choice=tool_choice,
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
            if tool_choice != "required" or not _is_unsupported_tool_choice(exc):
                raise
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=openai_tools(),
                tool_choice="auto",
                temperature=0.6 if self.thinking else 0.4,
                top_p=0.95,
                max_tokens=1024,
                extra_body={
                    "chat_template_kwargs": {"enable_thinking": self.thinking},
                },
            )

    def diagnose(self, query: str) -> DiagnosisResult:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        query = query.strip()

        rollout_writer = RolloutWriter(self.rollout_dir) if self.rollout else NullRolloutWriter()
        rollout_writer.append(
            {"event": "query", "query": query, "model": self.model, "max_steps": self.max_steps}
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": query},
        ]
        events: list[ToolEvent] = []
        seen_calls: set[tuple[str, str]] = set()
        # Track repeated tool names to detect when the model is stuck calling the
        # same tool over and over without converging on an answer.
        last_tool_name: str | None = None
        tool_name_streak = 0
        # Consecutive malformed tool usages (unknown tool, invalid arguments,
        # duplicates). Reset by any successful tool call; tool findings that
        # report network failures deliberately do not reset or increment it.
        consecutive_model_errors = 0
        backend_errors = 0
        completed_turns = 0
        non_convergence_reason: str | None = None
        # Overwritten at every real exit; only an unexpected exception keeps
        # "crash", which is exactly what the rollout record should say then.
        exit_status = "crash"

        try:
            for turn in range(1, self.max_steps + 1):
                # Force a tool call on the first turn so the model must gather evidence
                # before speaking. This eliminates the failure mode where the small model
                # answers from priors ("I don't have that tool") instead of checking.
                choice = "required" if turn == 1 else "auto"
                response = None
                while response is None:
                    try:
                        response = self._create_with_fallback(messages, tool_choice=choice)
                    except Exception as exc:
                        backend_errors += 1
                        if not events:
                            exit_status = "backend_error"
                            raise NetworkDoctorError(f"MiniCPM5 request failed: {exc}") from exc
                        if backend_errors >= 2:
                            non_convergence_reason = (
                                "the model backend became unavailable mid-diagnosis "
                                f"({exc.__class__.__name__}: {str(exc)[:160]})"
                            )
                            break
                        if self.backend_retry_delay:
                            time.sleep(self.backend_retry_delay)
                if response is None:
                    exit_status = "backend_error"
                    break
                completed_turns += 1

                message = response.choices[0].message
                calls = getattr(message, "tool_calls", None) or []
                if not calls:
                    content = (getattr(message, "content", None) or "").strip()
                    if not content:
                        exit_status = "empty_response"
                        raise NetworkDoctorError("MiniCPM5 returned neither text nor tool calls")
                    exit_status = "completed"
                    return DiagnosisResult(
                        text=content,
                        model_turns=completed_turns,
                        tool_events=tuple(events),
                        rollout_path=rollout_writer.path,
                    )

                messages.append(_assistant_message(message))
                rollout_writer.append(
                    {
                        "event": "assistant",
                        "turn": completed_turns,
                        "tool_calls": [{"name": call.function.name} for call in calls],
                    }
                )
                turn_names: list[str] = []
                for call in calls:
                    name = call.function.name
                    try:
                        arguments = _parse_arguments(call.function.arguments)
                        signature = (
                            name,
                            json.dumps(arguments, sort_keys=True, ensure_ascii=False),
                        )
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
                    failure_class = _classify_tool_failure(result) if not result.get("ok") else ""
                    rollout_writer.append(
                        {
                            "event": "tool_result",
                            "turn": completed_turns,
                            "name": name,
                            "ok": bool(result.get("ok")),
                            "error_class": failure_class,
                            **({"error": result["error"]} if result.get("error") else {}),
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": tool_result_json(result),
                        }
                    )
                    turn_names.append(name)
                    if result.get("ok"):
                        consecutive_model_errors = 0
                    elif failure_class == "model_error":
                        consecutive_model_errors += 1

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
                        exit_status = "tool_name_streak"
                        break
                else:
                    last_tool_name = None
                    tool_name_streak = 0

                if consecutive_model_errors >= 3:
                    non_convergence_reason = (
                        f"the model made {consecutive_model_errors} consecutive "
                        "malformed tool calls (unknown tool, invalid arguments, "
                        "or duplicates)"
                    )
                    exit_status = "model_error_limit"
                    break

            if not events:
                exit_status = "no_evidence"
                raise StepLimitError(
                    f"MiniCPM5 did not finish after {self.max_steps} model turns; "
                    "retry with a more specific symptom"
                )

            if exit_status == "crash":
                exit_status = "step_limit"
            return DiagnosisResult(
                text=_build_partial_text(
                    tuple(events),
                    reason=non_convergence_reason
                    or f"the model did not converge within {self.max_steps} turns",
                ),
                model_turns=completed_turns,
                tool_events=tuple(events),
                rollout_path=rollout_writer.path,
            )
        finally:
            rollout_writer.finish(
                exit_status,
                model_turns=completed_turns,
                tool_events=len(events),
                query_chars=len(query),
            )
