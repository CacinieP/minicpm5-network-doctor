import pytest

from minicpm_network_doctor.scope import extract_targets, normalize_host, validate_tool_scope


def test_extract_targets_from_urls_hosts_ports_and_ipv6() -> None:
    targets = extract_targets(
        "curl https://Example.COM:8443/path failed; compare registry.npmjs.org:443 "
        "with db:5432, [2001:db8::1]:443, and 2001:db8::2"
    )

    assert targets == frozenset(
        {"example.com", "registry.npmjs.org", "db", "2001:db8::1", "2001:db8::2"}
    )


def test_extract_targets_handles_idna_url_and_ignores_plain_words() -> None:
    assert extract_targets("检查 https://例子.测试/path 中的错误") == frozenset(
        {"xn--fsqu00a.xn--0zwm56d"}
    )
    assert extract_targets("npm install just times out") == frozenset()


def test_normalize_host_handles_trailing_dot_and_ip_literals() -> None:
    assert normalize_host("Example.COM.") == "example.com"
    assert normalize_host("[2001:0db8::1]") == "2001:db8::1"


def test_validate_tool_scope_allows_local_context_and_reported_target() -> None:
    allowed = frozenset({"example.com"})

    assert validate_tool_scope("inspect_proxy_environment", {}, allowed) is None
    assert validate_tool_scope("resolve_dns", {"host": "EXAMPLE.com."}, allowed) is None
    assert validate_tool_scope("test_http", {"url": "https://example.com/a"}, allowed) is None


def test_validate_tool_scope_rejects_missing_or_unreported_target() -> None:
    allowed = frozenset({"example.com"})

    missing = validate_tool_scope("resolve_dns", {}, allowed)
    outside = validate_tool_scope("test_http", {"url": "https://other.example/"}, allowed)

    assert missing and missing["error"] == "target_missing_or_invalid"
    assert outside and outside["error"] == "target_out_of_scope"
    assert outside["allowed_hosts"] == ["example.com"]


@pytest.mark.parametrize(
    "query",
    [
        "Check https://example.com/other.example/report.json",
        "Check https://example.com/?target=other.example#third.example",
        "Check https://user.example:secret@EXAMPLE.com/",
        "Check https://example.com/[2001:db8::1]?server=db:5432",
        "Contact user@mail.other.example about example.com",
    ],
)
def test_extract_targets_does_not_authorize_embedded_hosts(query) -> None:
    assert extract_targets(query) == frozenset({"example.com"})


def test_extract_targets_retains_hosts_outside_urls() -> None:
    assert extract_targets("https://example.com/a.json and db:5432, other.example") == frozenset(
        {"example.com", "db", "other.example"}
    )
    assert extract_targets("Check [example.com]") == frozenset({"example.com"})


@pytest.mark.parametrize("query", ["https://[::1]", "[https://[::1]]", "[::1]:443"])
def test_extract_targets_preserves_ipv6_brackets(query) -> None:
    assert extract_targets(query) == frozenset({"::1"})


def test_extract_targets_validates_bracketed_ipv6_and_supports_zone_ids() -> None:
    assert extract_targets("[fe80::1%en0]:443") == frozenset({"fe80::1%en0"})
    assert extract_targets("[dead:beef:bad]") == frozenset()


@pytest.mark.parametrize(
    "query,expected",
    [
        ("2001:db8:12:34::1", "2001:db8:12:34::1"),
        ("2001:db8::192.0.2.1", "2001:db8::c000:201"),
    ],
)
def test_extract_targets_does_not_authorize_ipv6_substrings(query, expected) -> None:
    assert extract_targets(query) == frozenset({expected})


@pytest.mark.parametrize(
    "query", ["localhostname", "-bad.example", "bad-.example", "https://[bad.example"]
)
def test_extract_targets_does_not_match_partial_or_malformed_hosts(query) -> None:
    assert extract_targets(query) == frozenset()


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Check example.com.", "example.com"),
        ("Check example.com.:443", "example.com"),
        ("Check example.com:443.", "example.com"),
        ("Check example.com.:443,", "example.com"),
        ("Check localhost.", "localhost"),
        ("Check localhost.:30000;", "localhost"),
        ("Check localhost:30000.", "localhost"),
        ("Check db:5432!", "db"),
        ("Check example.com.evil/path", "example.com.evil"),
    ],
)
def test_extract_targets_accepts_terminal_dots_and_port_punctuation(query, expected) -> None:
    assert extract_targets(query) == frozenset({expected})


@pytest.mark.parametrize(
    "query", ["example.com.-bad", "example.com..", ".example.com", "example.com-"]
)
def test_extract_targets_terminal_dot_does_not_authorize_partial_hosts(query) -> None:
    assert extract_targets(query) == frozenset()
