# Changelog

All notable changes are documented here. The project follows semantic versioning.

## 0.3.2 — 2026-09-06

### Fixed

- Keep domains embedded in URL credentials, paths, queries, and fragments outside the target
  allowlist; preserve the actual authority when extracting IPv6 URLs.
- Handle malformed model-list metadata without an uncaught exception during server preflight.
- Redact malformed proxy URLs without exposing credentials through parser errors.
- Share the versioned User-Agent between HTTP diagnostics and model-server preflight.

### Added

- Offline tests with DNS/socket guards and isolated proxy/model environment settings.
- CI for Python 3.10–3.14 on Ubuntu, with macOS and Windows coverage on Python 3.12.
- Wheel/sdist builds, strict package metadata validation, dependency advisory checks, and clean
  installs outside the checkout on Linux, macOS, and Windows.
- Version, changelog, prompt-resource, skill metadata, and release tag consistency checks.
- A required aggregate CI result, retained test reports, and release artifacts with SHA-256 sums.
- Commit-pinned GitHub Actions with monthly Dependabot updates.

### Validation scope

- Tests and installed-package smoke checks require no Ollama, model weights, or model server.
- These checks do not establish real-model diagnosis accuracy; the benchmark remains pending.
- Distribution remains GitHub Releases only. PyPI publication is still deferred.

## 0.3.1 — 2026-09-02

### Changed

- Treat fake-IP classification as evidence of a proxy-managed DNS path, not proof of DNS failure,
  traffic loss, or proxy causality.
- Require symptom-specific evidence and a controlled direct/bypass comparison before attributing
  a failure to the proxy.
- Rewrite the English and Chinese examples to preserve uncertainty when TCP succeeds.
- Identify HTTP and model-server requests as version 0.3.1.

### Added

- Regression tests that keep deterministic fake-IP observations and the system prompt non-causal.
- A versioned 20-scenario, five-runs-per-configuration real-model benchmark plan for v0.4.0.
- A bounded pre-release Ollama F16 smoke record with explicit non-benchmark evidence limits.

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
