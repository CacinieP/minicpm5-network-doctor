# Changelog

All notable changes are documented here. The project follows semantic versioning.

## 0.5.0 — 2026-09-17

### Added

- `resolve_dns` accepts `resolver="doh:cloudflare"`, `"doh:google"`, or `"doh:quad9"`. The result
  carries the public DNS-over-HTTPS answer for the host **next to** the system-resolver answer plus
  a deterministic comparison observation, so a local DNS rewrite is visible in a single call. Plain
  UDP/53 to a public resolver is typically hijacked in TUN mode; DoH is not, which is what makes it
  a usable second path. Answers about another owner name, or unparsable data, are dropped instead of
  reported.
- `test_tcp` and `inspect_tls` accept an `address` argument: an IP literal for the reported host, so
  a connection or handshake can be attempted against one explicit resolution path. `inspect_tls`
  still uses the reported host for SNI and certificate validation. `scope.py` accepts `address`
  only when the host itself was reported and rejects a hostname there, so a controlled comparison
  cannot widen the target set.

### Fixed

- Fake-IP TCP semantics. `resolve_dns` and `test_tcp` now state that TCP success to a fake-IP
  address only proves that the local proxy accepted the connection and carries no information about
  the real upstream, and the system prompt no longer describes a successful TCP connection as
  evidence that "the mapped path is reachable". `test_tcp` reports `peer_classification` and
  attaches that note deterministically when the peer is a fake-IP address, instead of leaving the
  caveat to the prompt alone.
- The `cg nat` observation no longer claims the address "is typically injected"; it now says it is
  typically assigned by an overlay such as Tailscale, or used as a proxy address pool. The existing
  fake-IP wording test gained a sibling that locks the CG-NAT branch.

### Documentation

- New `docs/evidence/README.md` with the required-field checklist for evidence records (version and
  source state, environment, model residency, complete invocation, backend parameters, per-check
  outcome, waited time before any interruption, explicit non-claims).
- `docs/evidence/2026-09-02-ollama-f16-smoke.md` gained an addendum naming the fields that were not
  captured and therefore cannot be reconstructed, so its *Inconclusive* row is no longer citable as
  evidence about model behaviour.

### Verified

- Locally on Python 3.14.7: 182 tests pass (was 105 at v0.4.0), 89.23% branch coverage, `ruff check`
  and `ruff format --check` clean, `scripts/verify_metadata.py` passes for version 0.5.0.
- On a real Clash/Mihomo TUN fake-ip stack (`docs/evidence/2026-09-17-doh-comparison.md`): the
  system resolver returned `198.18.0.6` for `example.com` while `resolver="doh:cloudflare"` returned
  public Cloudflare addresses in the same call; `test_tcp` accepted the fake-IP peer in 6.5 ms with
  the caveat attached, `test_tcp(address=...)` reached the DoH-reported address in 8.1 ms, and
  `inspect_tls(address=...)` completed a TLSv1.3 handshake whose certificate is issued to
  `example.com`. One run per check, so no stability or accuracy claim.
- CI on the release commit is the authoritative evidence per `docs/publishing.md` and had not run
  yet when this entry was written.

## 0.4.0 — 2026-09-17

### Changed

- Switch the reference backend from SGLang to `llama-server` (llama.cpp). `MINICPM_BASE_URL`
  now defaults to `http://127.0.0.1:8080/v1` and `MINICPM_MODEL` to `minicpm5-2b`, matching the
  verified launch configuration; `agent.py` no longer hardcodes the old `openbmb/MiniCPM5-1B` name.
- Move MiniCPM5 to the 2B weights. `Q4_K_M` on llama-server b10150 is now the verified reference
  quantization, replacing the previous "prefer F16" guidance.
- Raise the completion budget from 1024 to 2048, and to 8192 under `--thinking`.
- `--thinking` also raises the default request timeout from 180s to 600s, since a full-budget
  thinking turn routinely exceeds it.

### Added

- `--max-tokens` to set the completion budget directly (256-32768), overriding the
  thinking-dependent defaults. Thinking variance measured 1.1k-25.5k characters of chain-of-thought
  on the reference model, so no single fixed value is safe.
- A `model_response_truncated_by_budget` warning and a llama.cpp evidence record
  ([docs/evidence/2026-09-17-llama-cpp-2b.md](docs/evidence/2026-09-17-llama-cpp-2b.md)).

### Fixed

- A model turn cut off by `finish_reason=length` no longer aborts the diagnosis. llama.cpp reports
  the cut-off chain of thought in `reasoning_content`, leaving both `content` and `tool_calls`
  empty; the agent treated that as a fatal "neither text nor tool calls" error and discarded every
  tool result collected so far. It now reports `model_response_truncated_by_budget` and returns the
  structured partial diagnosis with the evidence intact. A truncated *first* turn still raises
  `StepLimitError`, because there is no evidence to preserve.

### Verified

- On llama-server b10150 with MiniCPM5-2B `Q4_K_M`: `tool_choice="required"`,
  `chat_template_kwargs.enable_thinking`, and `reasoning_effort` are all accepted (HTTP 200), so
  none of the compatibility fallbacks fire. `--check-server` reports model id `minicpm5-2b`.
- A non-thinking diagnosis of an npm registry timeout returned all four required sections with
  correct fake-IP classification.

### Known gaps

- `--thinking` truncated again at the 8192 default on a multi-turn run, so 2B thinking does not
  reliably converge. The evidence now survives as a partial diagnosis with
  `model_response_truncated_by_budget`, but no diagnosis is produced. `--max-tokens` is the knob;
  a larger fixed default would only lengthen every turn against the 600s timeout ceiling.
- One symptom per mode, one run each. No scenario suite and no repetitions, so no accuracy or
  run-to-run stability claim is made.

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
