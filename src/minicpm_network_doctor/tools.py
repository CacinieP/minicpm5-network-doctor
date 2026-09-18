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

USER_AGENT = "minicpm-network-doctor/0.5.0"
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


# A TCP handshake that terminates inside a local TUN stack says nothing about the real
# upstream. One constant lets resolve_dns and test_tcp emit identical deterministic wording.
FAKE_IP_TCP_NOTE = (
    "TCP success to a fake-IP address only proves that the local proxy accepted the "
    "connection; it carries no information about the real upstream. Use test_http or "
    "inspect_tls for upstream evidence."
)

# Fixed public DoH endpoints. Plain UDP/53 to a public resolver is typically hijacked in
# TUN mode while DNS-over-HTTPS is not, which is what makes this a usable second
# resolution path for a controlled comparison.
_DOH_RESOLVERS: dict[str, str] = {
    "doh:cloudflare": "https://cloudflare-dns.com/dns-query",
    "doh:google": "https://dns.google/resolve",
    "doh:quad9": "https://dns.quad9.net/dns-query",
}
_DNS_RECORD_TYPES = {"A": 1, "AAAA": 28}
_DNS_CNAME_TYPE = 5
_MAX_DOH_BODY = 65_536


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


def _validate_explicit_address(address: str) -> str:
    """Validate an explicit IP literal used to reach an already-reported host.

    A hostname is rejected on purpose: an explicit *name* would let a call reach a host
    the user never reported, while an explicit address is only a different resolution
    path to the host that was reported.
    """
    if not isinstance(address, str):
        raise ValueError("address must be a string")
    normalized = _validate_host(address)
    try:
        ipaddress.ip_address(normalized.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError("address must be an IP literal, not a hostname") from exc
    return normalized


def _validate_resolver(resolver: str) -> str:
    if not isinstance(resolver, str):
        raise ValueError("resolver must be a string")
    value = resolver.strip().lower()
    if value != "system" and value not in _DOH_RESOLVERS:
        allowed = ", ".join(["system", *sorted(_DOH_RESOLVERS)])
        raise ValueError(f"resolver must be one of: {allowed}")
    return value


def _doh_lookup(host: str, record_type: str, endpoint: str, timeout: float) -> list[str]:
    """Query one fixed DoH endpoint and return the addresses it reports for ``host``."""
    query = urllib.parse.urlencode({"name": host, "type": record_type})
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/dns-json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_DOH_BODY + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"DNS-over-HTTPS resolver replied HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"DNS-over-HTTPS request failed: {_short_error(exc)}") from exc
    if len(body) > _MAX_DOH_BODY:
        raise ValueError("DNS-over-HTTPS response was unexpectedly large")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("DNS-over-HTTPS response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("DNS-over-HTTPS response was not a JSON object")
    status = payload.get("Status")
    if not isinstance(status, int) or status != 0:
        raise ValueError(f"DNS-over-HTTPS resolver reported status {status}")

    answers = payload.get("Answer")
    if answers is None:
        return []
    if not isinstance(answers, list):
        raise ValueError("DNS-over-HTTPS response had an unexpected Answer section")

    # Accept an answer only when its owner name is the queried host or a CNAME the
    # resolver already followed, so a response about another name cannot widen the check.
    owner_names = {host}
    addresses: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        name = str(answer.get("name") or "").strip().rstrip(".").lower()
        record = answer.get("type")
        data = answer.get("data")
        if not isinstance(data, str):
            continue
        if record == _DNS_CNAME_TYPE:
            owner_names.add(data.strip().rstrip(".").lower())
            continue
        if record != _DNS_RECORD_TYPES[record_type] or name not in owner_names:
            continue
        try:
            ipaddress.ip_address(data)
        except ValueError:
            continue
        if data not in addresses:
            addresses.append(data)
    return addresses


def _address_entries(addresses: list[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for address in addresses:
        family = "IPv6" if ":" in address else "IPv4"
        key = f"{family}:{address}"
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "family": family,
                "address": address,
                "classification": _classify_address(address),
            }
        )
    return entries


def _system_address_entries(host: str, port: int) -> list[dict[str, str]]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for family, _, _, _, sockaddr in records:
        address = sockaddr[0]
        if family == socket.AF_INET6:
            address = address.split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    return _address_entries(addresses)


def _path_labels(entries: list[dict[str, str]]) -> str:
    labels = sorted({entry["classification"] for entry in entries})
    return ", ".join(labels) if labels else "no addresses"


def _comparison_observation(
    resolver: str,
    resolver_entries: list[dict[str, str]],
    system_entries: list[dict[str, str]],
    system_error: str | None,
) -> str:
    if system_error:
        return (
            f"The system resolver failed ({system_error}), so only the {resolver} answer is "
            "available. That alone does not show which resolution path is correct."
        )
    if not system_entries:
        return (
            f"The system resolver returned no addresses while {resolver} returned "
            f"{_path_labels(resolver_entries)}; the local resolution path produced nothing."
        )
    resolver_labels = {entry["classification"] for entry in resolver_entries}
    system_labels = {entry["classification"] for entry in system_entries}
    if resolver_labels == system_labels:
        return (
            f"The system resolver and {resolver} agree on the classification "
            f"({_path_labels(system_entries)}), so the local DNS path is not rewriting "
            "this host."
        )
    return (
        f"The two resolution paths disagree: the system resolver returned "
        f"{_path_labels(system_entries)} while {resolver} returned "
        f"{_path_labels(resolver_entries)}. The local DNS answer is therefore not the "
        "public DNS answer; do not treat the system result as the destination address."
    )


def _observations_for(entries: list[dict[str, str]]) -> list[str]:
    """Deterministic, blame-free notes for the address classes in one result."""
    observations: list[str] = []
    non_public = sorted(
        {entry["classification"] for entry in entries if entry["classification"] != "public"}
    )
    for label in non_public:
        if label.startswith("fake-ip"):
            observations.append(
                f"The resolved address is in the {label} range, which is a reserved "
                "benchmarking block commonly used by Clash/Mihomo for synthetic "
                "fake-IP DNS mappings. This identifies a proxy-managed DNS path. "
                "By itself, the mapping does not establish a connectivity failure "
                f"or explain the reported symptom. {FAKE_IP_TCP_NOTE}"
            )
        elif label.startswith("cg nat"):
            observations.append(
                f"The resolved address is in the {label} range, which is not globally "
                "routable and is typically assigned by an overlay such as Tailscale, "
                "or used as a proxy address pool."
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
    if entries and all(entry["classification"] == "public" for entry in entries):
        observations.append("All resolved addresses are public routable IPs.")
    return observations


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


def resolve_dns(host: str, port: int = 443, resolver: str = "system") -> dict[str, Any]:
    """Resolve one host through the system resolver or a fixed public DoH endpoint.

    With ``resolver="system"`` (default) this is a plain system lookup. With a
    ``doh:*`` resolver the result also carries the system-resolver answer for the same
    host, so one call can show whether the local DNS path rewrites the destination.
    """
    checked_host = _validate_host(host)
    checked_port = _validate_port(port)
    checked_resolver = _validate_resolver(resolver)
    started = time.monotonic()

    system_error: str | None = None
    system_entries: list[dict[str, str]] = []
    try:
        system_entries = _system_address_entries(checked_host, checked_port)
    except Exception as exc:
        if checked_resolver == "system":
            raise
        system_error = _short_error(exc)

    if checked_resolver == "system":
        entries = system_entries
    else:
        addresses: list[str] = []
        for record_type in ("A", "AAAA"):
            addresses.extend(
                _doh_lookup(
                    checked_host,
                    record_type,
                    _DOH_RESOLVERS[checked_resolver],
                    timeout=_validate_timeout(8.0),
                )
            )
        entries = _address_entries(addresses)

    observations = _observations_for(entries)
    if checked_resolver != "system":
        observations.append(
            _comparison_observation(checked_resolver, entries, system_entries, system_error)
        )

    result: dict[str, Any] = {
        "ok": bool(entries),
        "host": checked_host,
        "resolver": checked_resolver,
        "addresses": entries[:8],
        "observations": observations,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }
    if checked_resolver != "system":
        # The comparison half of the result: what the local stack would have returned.
        result["system_addresses"] = system_entries[:8]
        result["system_resolver_error"] = system_error
    return result


def test_tcp(
    host: str,
    port: int,
    timeout: float = 5.0,
    address: str | None = None,
) -> dict[str, Any]:
    checked_host = _validate_host(host)
    checked_port = _validate_port(port)
    checked_timeout = _validate_timeout(timeout)
    checked_address = _validate_explicit_address(address) if address is not None else None
    connect_target = checked_address or checked_host
    started = time.monotonic()
    with socket.create_connection((connect_target, checked_port), timeout=checked_timeout) as conn:
        peer = conn.getpeername()
    peer_address = peer[0]
    classification = _classify_address(peer_address)
    observations = [FAKE_IP_TCP_NOTE] if classification.startswith("fake-ip") else []
    return {
        "ok": True,
        "host": checked_host,
        "port": checked_port,
        "address": checked_address,
        "connect_address": connect_target,
        "peer_address": peer_address,
        "peer_classification": classification,
        "observations": observations,
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


def inspect_tls(
    host: str,
    port: int = 443,
    timeout: float = 8.0,
    address: str | None = None,
) -> dict[str, Any]:
    """Validate TLS for ``host``, optionally against an explicit upstream address.

    ``address`` only changes where the TCP connection goes: the SNI and certificate
    checks still use the reported host, so the call stays a check of that host.
    """
    checked_host = _validate_host(host)
    checked_port = _validate_port(port)
    checked_timeout = _validate_timeout(timeout)
    checked_address = _validate_explicit_address(address) if address is not None else None
    connect_target = checked_address or checked_host
    context = ssl.create_default_context()
    set_alpn_protocols = getattr(context, "set_alpn_protocols", None)
    if set_alpn_protocols is not None:
        set_alpn_protocols(["h2", "http/1.1"])
    started = time.monotonic()
    with (
        socket.create_connection((connect_target, checked_port), timeout=checked_timeout) as raw,
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
        "address": checked_address,
        "connect_address": connect_target,
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
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        # Parser errors can quote the complete authority, including credentials.
        # An invalid local setting must never escape through the tool error path.
        return "[set; invalid URL hidden]"
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
        description=(
            "Resolve one hostname and report its IPv4 and IPv6 addresses. Use "
            'resolver="doh:cloudflare" (or doh:google, doh:quad9) to also get the '
            "system-resolver answer for the same host, which shows whether the local DNS "
            "path rewrites the destination."
        ),
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname or IP address to resolve."},
                "port": {
                    "type": "integer",
                    "description": "Service port used for address selection.",
                    "default": 443,
                },
                "resolver": {
                    "type": "string",
                    "description": (
                        "'system' for the local resolver (default), or a fixed public "
                        "DNS-over-HTTPS resolver: 'doh:cloudflare', 'doh:google', "
                        "'doh:quad9'."
                    ),
                    "default": "system",
                },
            },
            "required": ["host"],
            "additionalProperties": False,
        },
        handler=resolve_dns,
    ),
    "test_tcp": ToolSpec(
        name="test_tcp",
        description=(
            "Attempt one TCP connection to a specific host and port. Pass address to reach "
            "the reported host on an explicit IP instead of the local DNS answer."
        ),
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
                "address": {
                    "type": "string",
                    "description": (
                        "Optional IP literal to connect to, for a controlled comparison "
                        "against the reported host; a hostname is rejected."
                    ),
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
        description=(
            "Validate the TLS connection and summarize the peer certificate. Pass address to "
            "handshake against an explicit IP while still checking the reported host's SNI "
            "and certificate."
        ),
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
                "address": {
                    "type": "string",
                    "description": (
                        "Optional IP literal to connect to for a controlled comparison; the "
                        "SNI and certificate checks still use host. A hostname is rejected."
                    ),
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
