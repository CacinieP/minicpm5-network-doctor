from __future__ import annotations

import ipaddress
import re
import urllib.parse
from collections.abc import Collection
from typing import Any

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_HOST_PATTERN = re.compile(
    r"(?<![\w@./-])"
    r"(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)"
    r"\.?(?::\d{1,5})?(?![\w.-])",
    re.IGNORECASE,
)
_BRACKETED_IPV6_PATTERN = re.compile(r"\[([^\]\s]*:[^\]\s]+)](?::\d{1,5})?")
_SINGLE_LABEL_PORT_PATTERN = re.compile(
    r"(?<![\w@./-])([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?):\d{1,5}(?!\d)",
    re.IGNORECASE,
)

_UNTARGETED_TOOLS = frozenset({"inspect_proxy_environment", "system_network_context"})


def normalize_host(host: str) -> str:
    """Return a comparison-safe host without changing its network meaning."""
    value = host.strip().strip("[]").rstrip(".")
    if "%" in value:
        address, zone = value.split("%", 1)
        try:
            return f"{ipaddress.ip_address(address).compressed}%{zone}".lower()
        except ValueError:
            pass
    try:
        return ipaddress.ip_address(value).compressed.lower()
    except ValueError:
        return value.encode("idna").decode("ascii").lower()


def extract_targets(query: str) -> frozenset[str]:
    """Extract explicit network targets from a natural-language diagnostic query.

    This is intentionally conservative. A missed target causes the runtime to ask for
    a clearer prompt; a false positive could authorize an unrelated network request.
    """
    targets: set[str] = set()

    for match in _URL_PATTERN.finditer(query):
        candidate = match.group(0).rstrip(".,;:!?)}")
        # Keep the closing bracket belonging to an IPv6 authority, but remove
        # a prose/Markdown bracket around the complete URL.
        while candidate.endswith("]") and candidate.count("]") > candidate.count("["):
            candidate = candidate[:-1]
        try:
            hostname = urllib.parse.urlsplit(candidate).hostname
            if hostname:
                targets.add(normalize_host(hostname))
        except (UnicodeError, ValueError):
            continue

    # A domain in a URL path, query, fragment, or userinfo is not the destination.
    # Do not let the standalone-host recognizers expand the authorized scope
    # with those strings, even if the URL itself was malformed.
    remaining = _URL_PATTERN.sub(" ", query)

    for match in _BRACKETED_IPV6_PATTERN.finditer(remaining):
        try:
            ipaddress.IPv6Address(match.group(1).split("%", 1)[0])
            targets.add(normalize_host(match.group(1)))
        except (UnicodeError, ValueError):
            continue

    remaining = _BRACKETED_IPV6_PATTERN.sub(" ", remaining)

    # IPv6 literals without brackets are unambiguous only when they do not carry
    # a port. Token parsing lets ipaddress perform the strict validation.
    standalone = list(remaining)
    for match in re.finditer(r"\S+", remaining):
        candidate = match.group(0).strip(".,;!?()[]{}<>\"'")
        if candidate.count(":") < 2:
            continue
        try:
            address = candidate.split("%", 1)[0]
            ipaddress.ip_address(address)
        except ValueError:
            continue
        targets.add(normalize_host(candidate))
        # A numeric IPv6 segment is not a hostname:port pair, and an embedded
        # IPv4 suffix is not a separately authorized destination.
        standalone[match.start() : match.end()] = " " * (match.end() - match.start())
    remaining = "".join(standalone)

    for match in _SINGLE_LABEL_PORT_PATTERN.finditer(remaining):
        targets.add(normalize_host(match.group(1)))

    for match in _HOST_PATTERN.finditer(remaining):
        candidate = match.group(0)
        if candidate.count(":") == 1:
            candidate = candidate.rsplit(":", 1)[0]
        try:
            targets.add(normalize_host(candidate))
        except (UnicodeError, ValueError):
            continue

    return frozenset(targets)


def _tool_target(name: str, arguments: dict[str, Any]) -> str | None:
    if name in _UNTARGETED_TOOLS:
        return None
    if "host" in arguments and isinstance(arguments["host"], str):
        return arguments["host"]
    if "url" in arguments and isinstance(arguments["url"], str):
        try:
            return urllib.parse.urlsplit(arguments["url"]).hostname
        except ValueError:
            return None
    return None


def validate_tool_scope(
    name: str,
    arguments: dict[str, Any],
    allowed_hosts: Collection[str],
) -> dict[str, Any] | None:
    """Return a safe tool error when a model tries an unreported network target."""
    target = _tool_target(name, arguments)
    if target is None:
        if name in _UNTARGETED_TOOLS:
            return None
        return {
            "ok": False,
            "error": "target_missing_or_invalid",
            "tool": name,
            "allowed_hosts": sorted(allowed_hosts),
        }

    try:
        normalized = normalize_host(target)
    except (UnicodeError, ValueError):
        normalized = target.strip().lower()
    if normalized in allowed_hosts:
        return None
    return {
        "ok": False,
        "error": "target_out_of_scope",
        "tool": name,
        "target": normalized,
        "allowed_hosts": sorted(allowed_hosts),
    }
