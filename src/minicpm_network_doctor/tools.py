from __future__ import annotations

import ipaddress
import json
import math
import os
import platform
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scope import normalize_host, validate_tool_scope

USER_AGENT = "minicpm-network-doctor/0.3.1"
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _validate_host(host: str) -> str:
    if not isinstance(host, str):
        raise ValueError("host must be a string")
    value = host.strip().rstrip(".")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if not value:
        raise ValueError("host is empty or too long")
    if "://" in value or "/" in value or any(char.isspace() for char in value):
        raise ValueError("host must be a hostname or IP address, not a URL")

    address = value
    zone: str | None = None
    if "%" in value:
        address, zone = value.rsplit("%", 1)
        if not zone or len(zone) > 64 or not re.fullmatch(r"[A-Za-z0-9_.-]+", zone):
            raise ValueError("host has an invalid IPv6 zone identifier")
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError:
        parsed_address = None
    if parsed_address is not None:
        if zone is not None and parsed_address.version != 6:
            raise ValueError("a zone identifier is only valid for IPv6")
        suffix = f"%{zone}" if zone is not None else ""
        return f"{parsed_address.compressed}{suffix}".lower()

    try:
        ascii_host = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("host is not a valid hostname") from exc
    invalid_label = any(not _HOST_LABEL.fullmatch(label) for label in ascii_host.split("."))
    if len(ascii_host) > 253 or invalid_label:
        raise ValueError("host is not a valid hostname")
    return ascii_host


def _validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return port


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number")
    value = float(timeout)
    if not math.isfinite(value):
        raise ValueError("timeout must be finite")
    return max(0.5, min(value, 10.0))


def _classify_address(address: str) -> str:
    """Classify an IP address into a short human-readable category.

    Returns one of: ``public`` or a reserved-block label such as
    ``fake-ip (198.18.0.0/15)``, ``loopback``, ``private``, ``link-local``,
    ``documentation``, or ``reserved``. Surfacing this label lets the small
    model recognize special-use and proxy-managed address paths without
    treating the classification itself as proof of a fault.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "invalid"

    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"

    networks = {
        "10.0.0.0/8": "private",
        "172.16.0.0/12": "private",
        "192.168.0.0/16": "private",
        "fc00::/7": "private",
        # RFC 2544 benchmarking range, reused by Clash/Mihomo as the default
        # pool for synthetic fake-IP DNS mappings.
        "198.18.0.0/15": "fake-ip (198.18.0.0/15)",
        # RFC 6598 carrier-grade NAT (100.64.0.0/10), also reused by
        # Tailscale and some proxies as an overlay address pool.
        "100.64.0.0/10": "cg nat (100.64.0.0/10)",
        "2001:db8::/32": "documentation",
        "192.0.2.0/24": "documentation",
        "198.51.100.0/24": "documentation",
        "203.0.113.0/24": "documentation",
    }
    for cidr, label in networks.items():
        if ip in ipaddress.ip_network(cidr):
            return label

    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved or ip.is_unspecified:
        return "reserved"
    return "public"


def _validate_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    value = url.strip()
    if len(value) > 2_048:
        raise ValueError("url must be 2048 characters or fewer")
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("url contains unsafe whitespace or control characters")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"url is invalid: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url scheme must be http or https")
    if not parsed.hostname:
        raise ValueError("url must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in diagnostic URLs")
    checked_host = _validate_host(parsed.hostname)
    if port is not None:
        _validate_port(port)
    netloc_host = f"[{checked_host}]" if ":" in checked_host else checked_host
    netloc = f"{netloc_host}:{port}" if port is not None else netloc_host
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, parsed.path, parsed.query, ""))


class _ScopedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only while they remain on the originally reported host."""

    def __init__(self, allowed_host: str, allowed_ports: set[int] | None = None) -> None:
        super().__init__()
        self.allowed_host = normalize_host(allowed_host)
        self.allowed_ports = allowed_ports or {80, 443}
        self.blocked_target_host: str | None = None
        self.blocked_target_port: int | None = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            parsed = urllib.parse.urlsplit(newurl)
            target = _validate_host(parsed.hostname or "")
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                raise ValueError("unsafe redirect URL")
            target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except (UnicodeError, ValueError):
            self.blocked_target_host = "invalid"
            return None
        if normalize_host(target) != self.allowed_host:
            self.blocked_target_host = normalize_host(target)
            return None
        if target_port not in self.allowed_ports:
            self.blocked_target_host = normalize_host(target)
            self.blocked_target_port = target_port
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _short_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:500]


def _flatten_name(parts: Any) -> str | None:
    values: list[str] = []
    for group in parts or ():
        for key, value in group:
            values.append(f"{key}={value}")
    return ", ".join(values) or None


def resolve_dns(host: str, port: int = 443) -> dict[str, Any]:
    checked_host = _validate_host(host)
    checked_port = _validate_port(port)
    started = time.monotonic()
    records = socket.getaddrinfo(checked_host, checked_port, type=socket.SOCK_STREAM)
    addresses: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for family, _, _, _, sockaddr in records:
        address = sockaddr[0]
        family_name = "IPv6" if family == socket.AF_INET6 else "IPv4"
        key = (family_name, address)
        if key not in seen:
            seen.add(key)
            addresses.append(
                {
                    "family": family_name,
                    "address": address,
                    "classification": _classify_address(address),
                }
            )

    observations: list[str] = []
    non_public = sorted({a["classification"] for a in addresses if a["classification"] != "public"})
    for label in non_public:
        if label.startswith("fake-ip"):
            observations.append(
                f"The resolved address is in the {label} range, which is a reserved "
                "benchmarking block commonly used by Clash/Mihomo for synthetic "
                "fake-IP DNS mappings. This identifies a proxy-managed DNS path. "
                "By itself, the mapping does not establish a connectivity failure "
                "or explain the reported symptom."
            )
        elif label.startswith("cg nat"):
            observations.append(
                f"The resolved address is in the {label} range, which is not globally "
                "routable and is typically injected by an overlay such as Tailscale or "
                "a proxy."
            )
        elif label in {"private", "loopback", "link-local"}:
            observations.append(
                f"The resolved address is {label}, not a public address. A public "
                "hostname resolving locally usually means a local override, split-horizon "
                "DNS, or a proxy mapping."
            )
        elif label == "documentation":
            observations.append(
                f"The resolved address is in a documentation/test range ({label}); this "
                "is never a real server address and indicates DNS is returning a placeholder."
            )
    if addresses and all(a["classification"] == "public" for a in addresses):
        observations.append("All resolved addresses are public routable IPs.")

    return {
        "ok": bool(addresses),
        "host": checked_host,
        "addresses": addresses[:8],
        "observations": observations,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def test_tcp(host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    checked_host = _validate_host(host)
    checked_port = _validate_port(port)
    checked_timeout = _validate_timeout(timeout)
    started = time.monotonic()
    with socket.create_connection((checked_host, checked_port), timeout=checked_timeout) as conn:
        peer = conn.getpeername()
    return {
        "ok": True,
        "host": checked_host,
        "port": checked_port,
        "peer_address": peer[0],
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def test_http(url: str, timeout: float = 8.0, use_environment_proxy: bool = True) -> dict[str, Any]:
    checked_url = _validate_url(url)
    checked_timeout = _validate_timeout(timeout)
    checked_host = urllib.parse.urlsplit(checked_url).hostname
    if checked_host is None:  # Defensive: _validate_url already enforces this.
        raise ValueError("url must include a hostname")
    proxy_handler = (
        urllib.request.ProxyHandler() if use_environment_proxy else urllib.request.ProxyHandler({})
    )
    parsed_url = urllib.parse.urlsplit(checked_url)
    original_port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
    redirect_handler = _ScopedRedirectHandler(checked_host, {80, 443, original_port})
    opener = urllib.request.build_opener(proxy_handler, redirect_handler)
    started = time.monotonic()
    method = "HEAD"
    fallback_reason: str | None = None

    def send(request_method: str):
        headers = {"User-Agent": USER_AGENT}
        if request_method == "GET":
            # A byte range prevents downloading a response body when a server does
            # not implement HEAD. We never read the body, even if Range is ignored.
            headers["Range"] = "bytes=0-0"
        request = urllib.request.Request(checked_url, headers=headers, method=request_method)
        try:
            response = opener.open(request, timeout=checked_timeout)
            return response, response.status, response.geturl(), response.headers
        except urllib.error.HTTPError as exc:
            return exc, exc.code, exc.geturl(), exc.headers

    response, status, final_url, headers = send(method)
    response.close()
    if status in {405, 501}:
        fallback_reason = f"HEAD returned HTTP {status}"
        method = "GET"
        response, status, final_url, headers = send(method)
        response.close()

    return {
        "ok": True,
        "url": checked_url,
        "method": method,
        "status": status,
        "final_url": final_url,
        "redirect_blocked": redirect_handler.blocked_target_host is not None,
        "redirect_target_host": redirect_handler.blocked_target_host,
        "redirect_target_port": redirect_handler.blocked_target_port,
        "head_fallback_reason": fallback_reason,
        "used_environment_proxy": use_environment_proxy,
        "server": headers.get("Server"),
        "content_type": headers.get("Content-Type"),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def inspect_tls(host: str, port: int = 443, timeout: float = 8.0) -> dict[str, Any]:
    checked_host = _validate_host(host)
    checked_port = _validate_port(port)
    checked_timeout = _validate_timeout(timeout)
    context = ssl.create_default_context()
    set_alpn_protocols = getattr(context, "set_alpn_protocols", None)
    if set_alpn_protocols is not None:
        set_alpn_protocols(["h2", "http/1.1"])
    started = time.monotonic()
    with (
        socket.create_connection((checked_host, checked_port), timeout=checked_timeout) as raw,
        context.wrap_socket(raw, server_hostname=checked_host) as wrapped,
    ):
        certificate = wrapped.getpeercert()
        protocol = wrapped.version()
        cipher = wrapped.cipher()
        alpn_protocol = wrapped.selected_alpn_protocol()

    not_after = certificate.get("notAfter")
    expires_at: str | None = None
    days_remaining: int | None = None
    if not_after:
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=timezone.utc)
        expires_at = expires.isoformat()
        days_remaining = (expires - datetime.now(timezone.utc)).days

    return {
        "ok": True,
        "host": checked_host,
        "port": checked_port,
        "protocol": protocol,
        "cipher": cipher[0] if cipher else None,
        "alpn_protocol": alpn_protocol,
        "subject": _flatten_name(certificate.get("subject")),
        "issuer": _flatten_name(certificate.get("issuer")),
        "subject_alt_names": [
            value for kind, value in certificate.get("subjectAltName", ()) if kind == "DNS"
        ][:20],
        "subject_alt_names_truncated": sum(
            1 for kind, _ in certificate.get("subjectAltName", ()) if kind == "DNS"
        )
        > 20,
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _redact_proxy_value(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return "[set; value hidden]"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    auth = "***@" if parsed.username or parsed.password else ""
    try:
        parsed_port = parsed.port
    except ValueError:
        return "[set; invalid port hidden]"
    port = f":{parsed_port}" if parsed_port else ""
    return f"{parsed.scheme}://{auth}{host}{port}"


def inspect_proxy_environment() -> dict[str, Any]:
    values: dict[str, str | None] = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        values[key] = _redact_proxy_value(os.environ.get(key) or os.environ.get(key.lower()))

    bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    bypass_entries = [entry.strip() for entry in bypass.split(",") if entry.strip()]
    discovered = urllib.request.getproxies()
    effective = {
        key.upper(): _redact_proxy_value(discovered.get(key))
        for key in ("http", "https", "all", "socks")
        if discovered.get(key)
    }
    effective_bypass = discovered.get("no", "")
    effective_bypass_entries = [
        entry.strip() for entry in effective_bypass.split(",") if entry.strip()
    ]
    return {
        "ok": True,
        "proxy_variables": values,
        "no_proxy_entries": bypass_entries[:20],
        "no_proxy_truncated": len(bypass_entries) > 20,
        "effective_proxies": effective,
        "effective_no_proxy_entries": effective_bypass_entries[:20],
        "effective_no_proxy_truncated": len(effective_bypass_entries) > 20,
    }


def _hosts_file_path() -> Path:
    if platform.system() == "Windows":
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return Path(system_root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def inspect_hosts_file(host: str) -> dict[str, Any]:
    """Report only entries that map the requested host in the local hosts file."""
    checked_host = _validate_host(host)
    path = _hosts_file_path()
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > 256_000:
        raise ValueError("hosts file is unexpectedly large")

    matches: list[dict[str, Any]] = []
    wanted = checked_host.rstrip(".").lower()
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        fields = raw_line.partition("#")[0].split()
        if len(fields) < 2:
            continue
        address, *aliases = fields
        normalized_aliases = [alias.rstrip(".").lower() for alias in aliases]
        if wanted in normalized_aliases:
            matched_aliases = [alias for alias in aliases if alias.rstrip(".").lower() == wanted]
            matches.append(
                {
                    "address": address,
                    "classification": _classify_address(address),
                    "aliases": matched_aliases[:10],
                    "line": line_number,
                }
            )

    return {
        "ok": True,
        "host": checked_host,
        "overridden": bool(matches),
        "entries": matches[:10],
        "entries_truncated": len(matches) > 10,
    }


def system_network_context() -> dict[str, Any]:
    return {
        "ok": True,
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "proxy_environment": inspect_proxy_environment(),
    }


TOOLS: dict[str, ToolSpec] = {
    "resolve_dns": ToolSpec(
        name="resolve_dns",
        description="Resolve one hostname and report its IPv4 and IPv6 addresses.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname or IP address to resolve."},
                "port": {
                    "type": "integer",
                    "description": "Service port used for address selection.",
                    "default": 443,
                },
            },
            "required": ["host"],
            "additionalProperties": False,
        },
        handler=resolve_dns,
    ),
    "test_tcp": ToolSpec(
        name="test_tcp",
        description="Attempt one TCP connection to a specific host and port.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Target hostname or IP address."},
                "port": {"type": "integer", "description": "Target TCP port."},
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds, clamped to 0.5-10.",
                    "default": 5,
                },
            },
            "required": ["host", "port"],
            "additionalProperties": False,
        },
        handler=test_tcp,
    ),
    "test_http": ToolSpec(
        name="test_http",
        description=(
            "Send HEAD to one HTTP or HTTPS URL, block cross-host redirects, and report the "
            "response; use a ranged GET only when HEAD is unsupported."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full HTTP or HTTPS URL."},
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds, clamped to 0.5-10.",
                    "default": 8,
                },
                "use_environment_proxy": {
                    "type": "boolean",
                    "description": "Whether to honor local proxy environment variables.",
                    "default": True,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=test_http,
    ),
    "inspect_tls": ToolSpec(
        name="inspect_tls",
        description="Validate the TLS connection and summarize the peer certificate.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "TLS server hostname."},
                "port": {
                    "type": "integer",
                    "description": "TLS server port.",
                    "default": 443,
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds, clamped to 0.5-10.",
                    "default": 8,
                },
            },
            "required": ["host"],
            "additionalProperties": False,
        },
        handler=inspect_tls,
    ),
    "inspect_proxy_environment": ToolSpec(
        name="inspect_proxy_environment",
        description=(
            "Read environment and platform-effective proxy settings with credentials redacted."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=inspect_proxy_environment,
    ),
    "inspect_hosts_file": ToolSpec(
        name="inspect_hosts_file",
        description=(
            "Check whether one reported hostname is overridden in the local hosts file; "
            "return only matching entries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Reported hostname to check."},
            },
            "required": ["host"],
            "additionalProperties": False,
        },
        handler=inspect_hosts_file,
    ),
    "system_network_context": ToolSpec(
        name="system_network_context",
        description="Report operating-system context and redacted proxy environment settings.",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=system_network_context,
    ),
}


def openai_tools() -> list[dict[str, Any]]:
    return [spec.as_openai_tool() for spec in TOOLS.values()]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    allowed_hosts: frozenset[str] | None = None,
) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        return {"ok": False, "error": "unknown_tool", "tool": name}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "arguments_must_be_an_object", "tool": name}
    if allowed_hosts is not None:
        scope_error = validate_tool_scope(name, arguments, allowed_hosts)
        if scope_error is not None:
            return scope_error
    try:
        return spec.handler(**arguments)
    except Exception as exc:
        return {
            "ok": False,
            "error": _short_error(exc),
            "error_type": exc.__class__.__name__,
            "tool": name,
        }


def tool_result_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
