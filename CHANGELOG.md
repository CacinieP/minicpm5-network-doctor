# Changelog

All notable changes are documented here. The project follows semantic versioning.

## 0.3.0 — 2026-09-02

### Added

- Runtime target extraction and enforcement for all host- and URL-based tools.
- `inspect_hosts_file`, limited to entries matching the reported hostname.
- HTTP `HEAD` fallback to a ranged `GET` for services returning 405 or 501.
- TLS ALPN and bounded subject-alternative-name evidence.
- `--check-server`, `--progress`, `--max-tool-calls`, and `--skip-server-check` CLI options.
- Versioned JSON output with `status`, `targets`, and `warnings`.
- Authenticated `/models` preflight with a 1 MB response limit.
- Automatic direct connections for loopback model endpoints, with explicit proxy override flags.
- Bounded model API retry policy and machine-readable JSON errors.
- OpenAI-compatible requests send `reasoning_effort` alongside the SGLang chat-template flag, so
  current Ollama releases can actually disable reasoning when `--thinking` is absent.
- Model timeouts are applied to the direct HTTP client as well as the SDK request layer, avoiding
  an unintended short default during first model load.
- Coverage enforcement and expanded tests for safety, CLI, tools, and agent convergence.

### Changed

- Ungrounded plain-text answers are discarded until usable tool evidence exists.
- The agent accepts at most four tool calls per turn and 12 executed calls by default.
- Partial fallback diagnoses match Chinese prompts with a Chinese response.
- HTTP and model-server requests identify version 0.3 in their user agent.
- Proxy inspection reports both environment variables and platform-effective settings.
- Host and URL validation now rejects malformed labels, non-finite timeouts, control characters,
  invalid ports, and oversized URLs before network access.

### Security

- Network targets outside the original user report return `target_out_of_scope` without executing.
- Model-server URLs reject embedded credentials and non-HTTP schemes.
- HTTP checks block cross-host redirects, and model preflight blocks cross-origin redirects so
  authorization headers cannot be forwarded outside the configured endpoint.
- Release preparation verifies that the Git tag, project metadata, and package version agree.
- Release artifact upload/download actions are updated to their current supported major versions.
- Hosts-file inspection never returns unrelated mappings and rejects unexpectedly large files.

### Removed

- Removed automatic PyPI publication from version-tag pushes. PyPI distribution is deferred;
  GitHub Releases are the only public release channel for v0.3.0.

## 0.2.0

- Added evidence-first tool choice, duplicate-call handling, graceful partial diagnoses, fake-IP
  classification, endpoint preflight, bilingual documentation, and PyPI publishing automation.
