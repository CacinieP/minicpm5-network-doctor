<p align="center">
  <strong>MiniCPM Network Doctor</strong>
</p>

<p align="center">
  Investigate unexpected DNS mappings, dead ports, and broken TLS with a 1B model and seven
  read-only tools, all running on your own machine.
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/CacinieP/minicpm5-network-doctor/ci.yml?branch=main&style=flat-square" alt="CI">
  <img src="https://img.shields.io/github/v/release/CacinieP/minicpm5-network-doctor?style=flat-square&color=blue" alt="Release">
  <img src="https://img.shields.io/badge/MiniCPM5-1B-blue" alt="MiniCPM5-1B">
  <img src="https://img.shields.io/badge/Python-3.10%2B-green" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-offline%20suite-success" alt="Tests">
  <img src="https://img.shields.io/badge/coverage-minimum%2085%25-success" alt="Coverage">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT License">
</p>

<p align="center">
  <a href="./README_CN.md">中文文档</a> ·
  <a href="#see-it-work">Demo</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#safety-boundary">Safety</a> ·
  <a href="./CHANGELOG.md">Changelog</a> ·
  <a href="./docs/benchmark-plan.md">Benchmark plan</a> ·
  <a href="#known-limitations">Limitations</a> ·
  <a href="#development">Development</a>
</p>

---

MiniCPM Network Doctor combines a locally served
[MiniCPM5-1B](https://github.com/OpenBMB/MiniCPM) model with a small set of read-only network
tools. The model decides which check is useful; deterministic Python code performs the check and
returns evidence.

The project is intentionally narrow: diagnose developer-facing DNS, TCP, HTTP, TLS, local port,
proxy environment, and package download problems without giving the model arbitrary shell access.

## See it work

A typical run — a developer reports that `npm install` times out. The agent resolves the host,
recognizes a proxy-managed fake-IP mapping, and states what that evidence can and cannot establish:

```text
$ minicpm-network-doctor \
    "npm install times out fetching registry.example.org; check the likely network cause"

Diagnosis: registry.example.org resolves to a fake-IP mapping managed by a local proxy.
This confirms that DNS is on a proxy-managed path. The TCP connection to the mapped
address succeeded, so the current evidence does not establish that the mapping or proxy
caused the npm timeout.

Evidence:
- resolve_dns (registry.example.org) -> address=198.18.0.42, classification=fake-ip (198.18.0.0/15)
  observation: "proxy-managed DNS path; mapping alone does not establish a connectivity failure"
- test_tcp (registry.example.org:443) -> ok, peer=198.18.0.42, 3.1ms

Recommended action: temporarily add only registry.example.org to the proxy's direct/bypass
list and retry once as a controlled comparison. Roll back by removing that one rule.

Verification: curl -v --connect-timeout 10 https://registry.example.org/
```

Every value under Evidence is produced by the read-only tools and surfaced verbatim. The model's
diagnosis may still be uncertain; it must not turn a special-use address classification into an
unsupported root-cause claim. For the full tool trace, pass `--json`.

## Why This Project

- **Local-first** — once the model is available locally, diagnosis does not depend on an external
  model API.
- **Evidence before advice** — the model sees actual tool results before proposing a cause.
- **Read-only execution** — the runtime observes network state but never changes configuration.
- **Small-model friendly** — code controls the loop, schemas, limits, and duplicate-call handling.
- **Bilingual** — the model is instructed to answer in the user's language.

## What's New in 0.3

- **Runtime target scoping** — network calls are rejected unless their host appeared explicitly in
  the user's report; this safety boundary no longer depends on prompt compliance.
- **No evidence, no diagnosis** — a backend that ignores forced tool choice is corrected and
  retried. Plain-text guesses are never accepted as completed diagnoses.
- **More accurate HTTP checks** — services that reject `HEAD` with 405/501 are retried with a
  ranged `GET` without reading the response body; redirects to unreported hosts are blocked.
- **Local override detection** — `inspect_hosts_file` reveals only entries matching the reported
  host, without exposing unrelated mappings.
- **Operational CLI** — `--check-server`, `--progress`, `--max-tool-calls`, stable JSON status, and
  model-list discovery make local setup and automation easier to debug.
- **Reliable local endpoints** — loopback model servers bypass environment/system proxies by
  default, while remote endpoints retain proxy support; `--use-model-proxy` and
  `--no-model-proxy` make the policy explicit.
- **Backend-aware thinking control** — requests send both the SGLang chat-template switch and the
  OpenAI-compatible `reasoning_effort` field used by current Ollama releases.

## Architecture

```text
Developer symptom or error
          │
          ▼
 MiniCPM5-1B via SGLang
          │ OpenAI-compatible tool_calls
          ▼
  Allowlisted Python tools
  DNS · TCP · HTTP · TLS · proxy env · hosts file · system context
          │
          ▼
 Evidence → diagnosis → one reversible recommendation → verification
```

SGLang is the recommended backend because MiniCPM5's official `minicpm5` parser converts the
model's XML-style calls into standard OpenAI-compatible `tool_calls`.

## Read-Only Tools

| Tool | Purpose |
|------|---------|
| `resolve_dns` | Resolve one hostname and report IPv4/IPv6 results |
| `test_tcp` | Attempt one connection to a specified host and port |
| `test_http` | Send HEAD, block cross-host redirects, and use a bodyless ranged GET on 405/501 |
| `inspect_tls` | Validate TLS and summarize the peer certificate |
| `inspect_proxy_environment` | Read environment and platform-effective proxies, with credentials redacted |
| `inspect_hosts_file` | Check one reported host for a local hosts-file override |
| `system_network_context` | Report OS context and redacted proxy settings |

There is no arbitrary command or port-scanning tool.

## Agent Behavior

Four runtime controls keep the small model honest and resilient:

- **Evidence-first first turn.** The first model turn is sent with
  `tool_choice="required"`, so the model must call a diagnostic tool before it is
  allowed to answer. This prevents the common small-model failure of answering
  from priors ("I don't have that tool") instead of checking. If a backend rejects
  `required`, the request is retried once with `auto`. If a backend accepts but ignores the
  requirement, its ungrounded answer is discarded and the runtime asks for evidence again.
- **Target scope enforcement.** Hostnames, IP addresses, and URLs are extracted from the original
  report. Any network tool call outside that allowlist receives `target_out_of_scope` and is not
  executed.
- **Bounded work.** At most four calls are accepted from one model turn and the diagnosis-wide
  execution budget defaults to 12 calls (configurable up to 24).
- **Graceful non-convergence.** If the model calls the same tool three turns in a
  row without finishing, or exhausts the turn budget, the runtime stops and
  returns a structured **partial diagnosis** — the four sections are still
  produced, but the Diagnosis section notes that the model did not converge and
  the Evidence section lists every result collected so far. The CLI therefore
  returns the gathered evidence with exit code 0 instead of failing hard. A hard
  `StepLimitError` (exit code 1) is reserved for the rare case where zero evidence
  was collected.

## Quick Start

> **Distribution status:** releases are distributed through GitHub Releases. This package is not
> currently published on PyPI; install it from the repository as shown below.

This is an independent community project and is not affiliated with or endorsed by OpenBMB.

### 1. Serve MiniCPM5 with tool-call parsing

The official MiniCPM deployment skill currently recommends installing SGLang from `main` for the
MiniCPM5 parser:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "git+https://github.com/sgl-project/sglang.git@main#subdirectory=python"

python -m sglang.launch_server \
  --model-path openbmb/MiniCPM5-1B \
  --served-model-name openbmb/MiniCPM5-1B \
  --port 30000 \
  --tool-call-parser minicpm5
```

This SGLang path targets an NVIDIA GPU environment. Other OpenAI-compatible runtimes can be used
when they return MiniCPM5 calls as native `tool_calls`.

<details>
<summary><b>Alternative backend: Ollama (macOS / no NVIDIA GPU)</b></summary>

SGLang only ships Linux wheels, so on macOS (Apple Silicon) or any machine without an NVIDIA GPU,
use [Ollama](https://ollama.com) to serve the same model with an OpenAI-compatible endpoint.
Tool calling works with the `minicpm5` chat template; the F16 GGUF is recommended for reliable
tool-call generation.

```bash
# Pull the F16 GGUF (recommended for stable tool calls)
ollama pull hf.co/openbmb/MiniCPM5-1B-GGUF:F16

# The OpenAI-compatible endpoint is on http://127.0.0.1:11434/v1
```

Then point Network Doctor at it:

```bash
minicpm-network-doctor \
  --base-url http://127.0.0.1:11434/v1 \
  --api-key ollama \
  --model hf.co/openbmb/MiniCPM5-1B-GGUF:F16 \
  "npm install times out fetching registry.npmjs.org"
```

> **Quantization note for Ollama:** heavily quantized variants (e.g. `Q4_K_M`) can still produce
> tool calls but may drift. Prefer `F16` for the most reliable diagnosis loop.

</details>

> **Quantization note:** use the full-precision / `F16` (or `bf16`) weights. Heavily quantized
> variants (e.g. `Q4_K_M`) tend to emit long chain-of-thought and never produce a structured
> `tool_calls` response, so the agent loop cannot run.

### 2. Install Network Doctor

```bash
git clone https://github.com/CacinieP/minicpm5-network-doctor.git
cd minicpm5-network-doctor

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Diagnose

```bash
minicpm-network-doctor \
  "npm install times out while fetching registry.npmjs.org; check the likely network cause"
```

Harder cases can enable MiniCPM5 thinking mode:

```bash
minicpm-network-doctor --thinking \
  "HTTPS to registry.npmjs.org works in a browser but the package manager reports a certificate error"
```

Print the complete tool trace as JSON:

```bash
minicpm-network-doctor --json "Check why https://example.com returns an error"
```

Check the endpoint before starting a slower model turn, or watch evidence arrive in real time:

```bash
minicpm-network-doctor --check-server
minicpm-network-doctor --progress "TLS fails for https://example.com"
```

Loopback endpoints such as Ollama at `127.0.0.1` connect directly by default so a desktop proxy
cannot intercept the model request. Remote endpoints continue to honor proxy settings. Override
either decision with `--use-model-proxy` or `--no-model-proxy`. Model API retries default to zero
to keep a slow local failure bounded; use `--max-retries` only when a remote endpoint benefits
from retries.

`--json` includes `schema_version`, `status` (`complete`, `partial`, or `error`), `targets`,
`warnings`, and the complete `tool_events` trace. Progress is written to stderr, so it is safe to
combine with machine-readable JSON on stdout.

## Configuration

| Environment variable | Default |
|----------------------|---------|
| `MINICPM_BASE_URL` | `http://127.0.0.1:30000/v1` |
| `MINICPM_MODEL` | `openbmb/MiniCPM5-1B` |
| `MINICPM_API_KEY` | `not-needed` |

Equivalent CLI flags are available through:

```bash
minicpm-network-doctor --help
```

## Agent Skill

The repository includes a Codex/Claude Code-style Agent Skill:

```text
skills/minicpm-network-doctor/
├── SKILL.md
└── agents/
    └── openai.yaml
```

For Codex, copy the folder into your personal skills directory:

```bash
cp -R skills/minicpm-network-doctor ~/.codex/skills/
```

The Skill teaches an agent to collect the exact symptom, invoke the local doctor, preserve tool
evidence, and separate diagnosis from verification.

## Safety Boundary

See [SECURITY.md](SECURITY.md) for the maintained security policy and vulnerability-reporting
guidance.

- Only the seven declared tools can execute.
- Network tools are runtime-scoped to targets explicitly present in the original report.
- Hosts, ports, URLs, timeouts, and tool arguments are validated.
- Diagnostic URLs cannot contain credentials.
- HTTP checks do not follow redirects to a host outside the original report.
- Model preflight does not forward API keys across redirect origins; loopback endpoints bypass
  proxies unless the user explicitly opts in.
- Proxy credentials are redacted before results reach the model.
- Duplicate tool calls are blocked.
- Per-turn and diagnosis-wide tool budgets are bounded; broad scans are not supported.
- The system prompt prohibits disabling certificate verification.
- Configuration changes are suggestions only; the runtime never applies them.

## Known Limitations

MiniCPM5-1B is a 1B-parameter model. The runtime adds several guardrails to
compensate, but some failure modes are inherent to the model and cannot be fully
solved in code:

- **Tool calling is not always reliable.** Some backends (notably Ollama) accept
  `tool_choice="required"` yet occasionally return plain text. The runtime rejects
  those ungrounded answers and retries, but a persistently non-compliant backend
  still ends with a clear no-evidence error instead of a diagnosis.
- **Quantization degrades tool-call stability.** Heavily quantized GGUFs
  (`Q4_K_M`) emit longer chain-of-thought and are more prone to drift or
  truncation than `F16`. Prefer `F16` for the most reliable diagnosis loop. The
  `--thinking` flag helps with hard cases but also consumes more of the token
  budget on reasoning, which can itself truncate the answer.
- **A fake-IP mapping is not a root cause.** It indicates a proxy-managed DNS
  path, which may be normal and healthy. Without a controlled direct/bypass
  comparison, the agent must leave the proxy's causal role unconfirmed.
- **No write access.** The agent only observes. It cannot flush DNS, toggle a
  proxy, restart a service, or apply any fix. Recommendations are reversible
  suggestions the user must run themselves.
- **No deep packet or route inspection.** Tools cover DNS, TCP, HTTP, TLS, and
  proxy environment. There is no `traceroute`, `tcpdump`, certificate-chain
  pinning check, or ability to inspect a connection that is silently dropped by a
  stateful firewall.
- **Single-host, symptom-driven scope.** Each diagnosis targets the host and
  symptom the user reports. There is no batch or continuous monitoring, and broad
  scans are intentionally unsupported.
- **Server-side randomness.** Diagnosis quality varies between runs at the same
  temperature. If a run stalls, the runtime returns a structured partial
  diagnosis (see [Agent Behavior](#agent-behavior)) so the evidence is not lost;
  retry with `--thinking` or a more specific symptom for a sharper result.
- **Backend-specific behavior.** SGLang (Linux/NVIDIA GPU) is the reference
  backend. Current Ollama releases honor OpenAI-compatible `reasoning_effort`,
  while SGLang uses `chat_template_kwargs.enable_thinking`; older or different
  runtimes may ignore one or both controls.

When the model behaves erratically, the most effective escalation is a stronger
model (e.g. MiniCPM5 4B/8B) on a backend that reliably parses tool calls, not
more loop guardrails.

## Project Structure

```text
minicpm-network-doctor/
├── src/minicpm_network_doctor/
│   ├── agent.py               # Tool-calling loop
│   ├── cli.py                 # Command-line interface
│   ├── scope.py               # Runtime target extraction and enforcement
│   ├── system_prompt.md       # Small-model diagnostic policy
│   └── tools.py               # Read-only diagnostic tools
├── skills/
│   └── minicpm-network-doctor/
├── docs/benchmark-plan.md     # Planned real-model E2E evaluation
├── tests/
├── .github/workflows/ci.yml
├── pyproject.toml
└── README_CN.md
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check .
ruff format --check .
pytest --cov=minicpm_network_doctor
python scripts/verify_metadata.py
```

CI covers Python 3.10–3.14 on Linux, plus Python 3.12 on macOS and Windows. Tests block
DNS and sockets and use fake OpenAI-compatible clients: no Ollama, model downloads, GPU,
API key, or running model server is required. CI also checks dependency advisories and installs
both the wheel and source distribution in fresh environments outside the checkout.
See [CI and release checks](docs/ci.md) for the matrix, coverage gate, and package smoke checks.
A manual end-to-end run can prove that the transport and tool loop work, but it does not establish
diagnostic accuracy or run-to-run stability. See the [real-model benchmark plan](docs/benchmark-plan.md)
for the evidence required before making reliability claims.

## Credits

- [OpenBMB/MiniCPM](https://github.com/OpenBMB/MiniCPM) for MiniCPM5-1B and its tool-calling
  deployment guidance.
- [CacinieP/network-troubleshoot-skill](https://github.com/CacinieP/network-troubleshoot-skill)
  for the evidence-first network troubleshooting workflow that inspired this runtime.

## License

[MIT](LICENSE)
