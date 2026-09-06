from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Any

from . import __version__
from .agent import NetworkDoctor, NetworkDoctorError, ToolEvent
from .tools import TOOLS, USER_AGENT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicpm-network-doctor",
        description="Run safe, read-only network diagnostics with a local MiniCPM server.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Exact symptom, error, host, and failed operation.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MINICPM_BASE_URL", "http://127.0.0.1:30000/v1"),
        help="OpenAI-compatible MiniCPM endpoint.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("MINICPM_API_KEY", "not-needed"),
        help="API key if the local endpoint requires one.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MINICPM_MODEL", "openbmb/MiniCPM5-1B"),
        help="Served model name.",
    )
    parser.add_argument("--max-steps", type=int, default=6, help="Maximum model turns (1-12).")
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=12,
        help="Maximum executed tool calls across the diagnosis (1-24).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Model API timeout in seconds. Each turn can take ~50s on slower runtimes.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Model API retries per turn (0-5). Local endpoints default to no retries.",
    )
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument(
        "--use-model-proxy",
        dest="use_model_proxy",
        action="store_true",
        help="Force the model-server connection through configured environment/system proxies.",
    )
    proxy_group.add_argument(
        "--no-model-proxy",
        dest="use_model_proxy",
        action="store_false",
        help="Connect directly to the model server, bypassing environment/system proxies.",
    )
    parser.set_defaults(use_model_proxy=None)
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
    parser.add_argument(
        "--check-server",
        action="store_true",
        help="Check the configured model endpoint, list served models, and exit.",
    )
    parser.add_argument(
        "--skip-server-check",
        action="store_true",
        help="Skip the fast GET /models preflight for compatible servers without that route.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print each read-only tool result to stderr while diagnosis runs.",
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


def _models_url(base_url: str) -> str:
    value = base_url.strip()
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("model server URL contains unsafe whitespace or control characters")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid model server URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model server URL must use http or https and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("model server URL must not contain credentials; use --api-key")
    if parsed.query or parsed.fragment:
        raise ValueError("model server URL must not contain a query or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("model server URL port must be between 1 and 65535")
    path = parsed.path.rstrip("/") + "/models"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _is_loopback_base_url(base_url: str) -> bool:
    parsed = urllib.parse.urlsplit(_models_url(base_url))
    host = (parsed.hostname or "").rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _should_use_model_proxy(base_url: str, override: bool | None) -> bool:
    if override is not None:
        return override
    return not _is_loopback_base_url(base_url)


def _server_origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme.lower(), host, port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent model API keys from being forwarded to another redirect origin."""

    def __init__(self, origin: tuple[str, str, int]) -> None:
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            if _server_origin(newurl) != self.origin:
                return None
        except ValueError:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_server_reachable(
    base_url: str,
    timeout: float = 2.5,
    *,
    api_key: str | None = None,
    use_environment_proxy: bool = True,
) -> dict[str, Any]:
    """Quickly probe the model endpoint before entering the slower diagnosis loop.

    Hits ``GET {base_url}/models`` with a short timeout. A model server that is down
    would otherwise force the user to wait for the full ``--timeout`` (default 180s)
    before the failure surfaces.
    """
    url = _models_url(base_url)
    headers = {"User-Agent": USER_AGENT}
    if api_key and api_key != "not-needed":
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    proxy_handler = (
        urllib.request.ProxyHandler() if use_environment_proxy else urllib.request.ProxyHandler({})
    )
    opener = urllib.request.build_opener(
        proxy_handler,
        _SameOriginRedirectHandler(_server_origin(url)),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status >= 400:
                raise NetworkDoctorError(
                    f"model server at {base_url} replied HTTP {response.status}; "
                    "is the MiniCPM5 endpoint healthy?"
                )
            reader = getattr(response, "read", None)
            body = reader(1_000_001) if reader is not None else b""
            if len(body) > 1_000_000:
                raise NetworkDoctorError("model server /models response exceeded 1 MB")
    except urllib.error.HTTPError as exc:
        raise NetworkDoctorError(
            f"model server at {base_url} replied HTTP {exc.code}; is the MiniCPM5 endpoint healthy?"
        ) from exc
    except urllib.error.URLError as exc:
        raise NetworkDoctorError(
            f"could not reach model server at {base_url} ({exc.reason}); "
            "start MiniCPM5 (or your OpenAI-compatible backend) before diagnosing."
        ) from exc
    except TimeoutError as exc:
        raise NetworkDoctorError(
            f"model server at {base_url} timed out after {timeout:g}s; "
            "check whether the endpoint is still loading or blocked by a proxy."
        ) from exc
    except OSError as exc:
        raise NetworkDoctorError(
            f"could not reach model server at {base_url} ({exc}); "
            "start MiniCPM5 (or your OpenAI-compatible backend) before diagnosing."
        ) from exc

    models: list[str] = []
    if body:
        try:
            payload = json.loads(body)
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                models = [
                    item["id"]
                    for item in data
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return {
        "ok": True,
        "base_url": base_url,
        "models": models,
        "proxy_mode": "environment" if use_environment_proxy else "direct",
    }


def _build_openai_client(
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    max_retries: int,
    use_environment_proxy: bool,
) -> Any:
    from openai import DefaultHttpxClient, OpenAI

    http_client = DefaultHttpxClient(
        trust_env=use_environment_proxy,
        timeout=timeout,
    )
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        http_client=http_client,
    )


def _emit_error(message: str, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {"schema_version": 1, "status": "error", "error": message},
                ensure_ascii=False,
            )
        )
    else:
        print(f"error: {message}", file=sys.stderr)


def _print_progress(event: ToolEvent) -> None:
    result = event.result
    state = "ok" if result.get("ok") else f"failed: {result.get('error', 'unknown error')}"
    target = result.get("host") or result.get("url") or "local context"
    duration = result.get("duration_ms")
    elapsed = f", {duration} ms" if duration is not None else ""
    print(f"[{event.name}] {target}: {state}{elapsed}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_tools:
        if args.json:
            print(json.dumps([spec.as_openai_tool() for spec in TOOLS.values()], indent=2))
        else:
            for spec in TOOLS.values():
                print(f"{spec.name}\t{spec.description}")
        return 0

    if not 1 <= args.max_steps <= 12:
        parser.error("--max-steps must be between 1 and 12")
    if not 1 <= args.max_tool_calls <= 24:
        parser.error("--max-tool-calls must be between 1 and 24")
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    if not 0 <= args.max_retries <= 5:
        parser.error("--max-retries must be between 0 and 5")

    if args.check_server:
        try:
            use_model_proxy = _should_use_model_proxy(args.base_url, args.use_model_proxy)
            server = _check_server_reachable(
                args.base_url,
                api_key=args.api_key,
                use_environment_proxy=use_model_proxy,
            )
        except (NetworkDoctorError, ValueError) as exc:
            _emit_error(str(exc), as_json=args.json)
            return 1
        if args.json:
            print(json.dumps(server, ensure_ascii=False, indent=2))
        else:
            print(f"Server reachable: {server['base_url']}")
            print(f"Connection: {server['proxy_mode']}")
            models = server["models"]
            print("Models: " + (", ".join(models) if models else "not reported"))
        return 0

    prompt = _read_prompt(args.prompt, parser)
    try:
        use_model_proxy = _should_use_model_proxy(args.base_url, args.use_model_proxy)
        client = _build_openai_client(
            base_url=args.base_url,
            api_key=args.api_key,
            timeout=args.timeout,
            max_retries=args.max_retries,
            use_environment_proxy=use_model_proxy,
        )
        if not args.skip_server_check:
            _check_server_reachable(
                args.base_url,
                api_key=args.api_key,
                use_environment_proxy=use_model_proxy,
            )
        doctor = NetworkDoctor(
            client,
            model=args.model,
            max_steps=args.max_steps,
            max_tool_calls=args.max_tool_calls,
            thinking=args.thinking,
            on_tool_event=_print_progress if args.progress else None,
        )
        result = doctor.diagnose(prompt)
    except (NetworkDoctorError, ValueError) as exc:
        _emit_error(str(exc), as_json=args.json)
        return 1

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.text)
        if result.warnings and args.progress:
            print("warnings: " + ", ".join(result.warnings), file=sys.stderr)
    return 0
