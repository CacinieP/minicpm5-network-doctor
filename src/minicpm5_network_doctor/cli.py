from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from . import __version__
from .agent import NetworkDoctor, NetworkDoctorError
from .tools import TOOLS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicpm5-network-doctor",
        description="Run safe, read-only network diagnostics with a local MiniCPM5 server.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Exact symptom, error, host, and failed operation.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MINICPM5_BASE_URL", "http://127.0.0.1:30000/v1"),
        help="OpenAI-compatible MiniCPM5 endpoint.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("MINICPM5_API_KEY", "not-needed"),
        help="API key if the local endpoint requires one.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MINICPM5_MODEL", "openbmb/MiniCPM5-1B"),
        help="Served model name.",
    )
    parser.add_argument("--max-steps", type=int, default=6, help="Maximum model turns (1-12).")
    parser.add_argument("--timeout", type=float, default=60.0, help="Model API timeout in seconds.")
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Enable MiniCPM5 thinking mode for harder diagnoses.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the answer and tool trace as JSON.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List available read-only tools and exit.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _read_prompt(parts: list[str], parser: argparse.ArgumentParser) -> str:
    if parts:
        return " ".join(parts).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    parser.error("provide a diagnostic prompt or pipe one through stdin")
    raise AssertionError("unreachable")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_tools:
        for spec in TOOLS.values():
            print(f"{spec.name}\t{spec.description}")
        return 0

    prompt = _read_prompt(args.prompt, parser)
    try:
        from openai import OpenAI

        client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout)
        doctor = NetworkDoctor(
            client,
            model=args.model,
            max_steps=args.max_steps,
            thinking=args.thinking,
        )
        result = doctor.diagnose(prompt)
    except (NetworkDoctorError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.text)
    return 0
