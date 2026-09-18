# Changelog

All notable changes are documented here. The project follows semantic versioning.

## 0.5.1 — 2026-09-18

### Fixed

- **Non-thinking mode did not converge.** The model called tools on every turn (up to
  `max_steps=6`) without producing a final text answer. After 3 turns with evidence, a
  synthesis nudge message is now injected instructing the model to stop calling tools
  and write the diagnosis in the required four-section format. Verified against
  llama-server + MiniCPM5-2B Q4_K_M: converges in 4 turns (29.7s) with `status=complete`.
- **Thinking mode timeout discarded all evidence.** A slow turn exceeding the 600s
  timeout raised `NetworkDoctorError` as a fatal error, losing every tool result gathered
  in prior turns. Timeout errors are now caught when evidence exists, returning a partial
  diagnosis with `model_request_timed_out` warning and exit code 0.

## 0.5.0 — 2026-09-17

Closes the remaining review items of issue #1: what a successful TCP connection to a fake-IP
address does and does not prove, and a way to actually run the controlled comparison the prompt
already asks for.

### Added

- `resolve_dns` now accepts `resolver="doh:cloudflare"`, `"doh:google"`, or `"doh:quad9"`. In one
  call you get the public resolver's answer **next to** your system's answer for the same hostname,
  plus a plain sentence saying whether the two agree. If your local DNS is rewriting the
  destination, you can see it here. The resolver is queried over HTTPS because plain DNS to a
  public server is usually intercepted in TUN mode.
- `test_tcp` and `inspect_tls` now accept `address=<IP>`, so a connection or TLS handshake can be
  made to one specific IP. `inspect_tls` still checks the certificate against the hostname you
  reported, so it verifies the host, not just the address.
- An IP in `address` is not treated as a new target: the hostname still has to be the one you
  reported, and a hostname in `address` is refused. A comparison cannot become a scan of another
  host.

### Fixed

- `test_tcp` reports what it actually connected to (`peer_address`, `peer_classification`) and, when
  that peer is a fake-IP address, attaches the caveat itself: the local proxy accepted the
  connection, which says nothing about the real server. Previously this only lived in the prompt, so
  it could be missed.
- The prompt no longer calls a successful TCP connection evidence that "the mapped path is
  reachable", which was easy to read as "the target is reachable".
- The note about `100.64.0.0/10` addresses no longer says the address "is typically injected"; it now
  says it is typically assigned by an overlay such as Tailscale, or used as a proxy's address pool.

### Documentation

- New `docs/evidence/README.md`: what an evidence record must contain before it counts as evidence
  (version and commit, environment, whether the model was already loaded, the exact command line,
  and how long you waited before interrupting anything).
- The 2026-09-02 Ollama record now says which of those fields were never captured, so its
  "inconclusive" result is no longer quoted as if it could be reproduced.

### Verified

- On a real Clash/Mihomo TUN setup (`docs/evidence/2026-09-17-doh-comparison.md`): `example.com`
  resolved to `198.18.0.6` locally while the same call through Cloudflare returned real Cloudflare
  addresses; `test_tcp` accepted the fake IP in 6.5 ms with the caveat attached; connecting to the
  real IP took 8.1 ms; and the TLS check against that IP returned a valid certificate for
  `example.com`. One run per check, so nothing here is a stability or accuracy claim.
- Tests: 182 passed, 89.23% branch coverage, lint and format clean.

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
