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
