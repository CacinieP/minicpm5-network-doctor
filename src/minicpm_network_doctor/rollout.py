"""Best-effort on-disk rollout logs: one JSONL file per diagnosis.

Every diagnosis writes its query, each tool result, and one final ``exit``
record carrying a machine-readable ``exit_status`` (``completed``,
``step_limit``, ``tool_name_streak``, ``model_error_limit``,
``backend_error``, ``empty_response``, ``no_evidence``, ``crash``). Records
are flushed line by line, so even a hard process crash leaves the events
that already happened on disk — the exit record is the only part it can
lose. All I/O failures disable the writer silently: logging must never
break the diagnosis itself.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENV_ROLLOUT_DIR = "MINICPM_NETWORK_DOCTOR_ROLLOUT_DIR"


def default_rollout_dir() -> Path:
    override = os.environ.get(ENV_ROLLOUT_DIR)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Application Support" / "minicpm-network-doctor" / "rollouts"
        )
    return Path.home() / ".local" / "state" / "minicpm-network-doctor" / "rollouts"


class RolloutWriter:
    """Append-only JSONL writer that never raises into the diagnosis loop."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory) if directory is not None else default_rollout_dir()
        self._path: Path | None = None
        self._failed = False

    @property
    def path(self) -> str:
        return str(self._path) if self._path is not None else ""

    def append(self, event: dict[str, Any]) -> None:
        if self._failed:
            return
        try:
            if self._path is None:
                self._directory.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                self._path = self._directory / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
            record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            # Rollout logging is diagnostics-for-diagnostics; if the location
            # is unwritable, keep diagnosing without it.
            self._failed = True

    def finish(self, exit_status: str, **fields: Any) -> None:
        self.append({"event": "exit", "exit_status": exit_status, **fields})


class NullRolloutWriter:
    """Interface-compatible no-op used when rollout logging is disabled."""

    path = ""

    def append(self, event: dict[str, Any]) -> None:
        _ = event

    def finish(self, exit_status: str, **fields: Any) -> None:
        _ = exit_status, fields
