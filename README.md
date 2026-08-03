<p align="center">
  <strong>MiniCPM5 Network Doctor</strong>
</p>

<p align="center">
  Catch the network problems an LLM can actually diagnose — DNS hijacking, dead ports, broken TLS —
  with a 1B model and six read-only tools, all running on your own machine.
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/CacinieP/minicpm5-network-doctor/ci.yml?branch=main&style=flat-square" alt="CI">
  <img src="https://img.shields.io/github/v/release/CacinieP/minicpm5-network-doctor?style=flat-square&color=blue" alt="Release">
  <img src="https://img.shields.io/badge/MiniCPM5-1B-blue" alt="MiniCPM5-1B">
  <img src="https://img.shields.io/badge/Python-3.10%2B-green" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/coverage-37%20tests-success" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT License">
</p>

<p align="center">
  <a href="./README_CN.md">中文文档</a> ·
  <a href="#see-it-work">Demo</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#safety-boundary">Safety</a> ·
  <a href="#known-limitations">Limitations</a> ·
  <a href="#development">Development</a>
</p>

---

MiniCPM5 Network Doctor combines a locally served
[MiniCPM5-1B](https://github.com/OpenBMB/MiniCPM) model with a small set of read-only network
tools. The model decides which check is useful; deterministic Python code performs the check and
returns evidence.

The project is intentionally narrow: diagnose developer-facing DNS, TCP, HTTP, TLS, local port,
proxy environment, and package download problems without giving the model arbitrary shell access.

## See it work

A typical run — a developer reports that `npm install` times out. The agent resolves the host,
spots that the address is in the fake-ip range injected by a local proxy, and pinpoints the cause:

```text
$ minicpm5-network-doctor \
    "npm install times out fetching registry.example.org; check the likely network cause"

Diagnosis: DNS for registry.example.org is being hijacked by a local proxy running in
fake-ip mode. The hostname resolves into the 198.18.0.0/15 reserved range instead of a
real server address, so the package request is intercepted or blackholed.

Evidence:
- resolve_dns (registry.example.org) -> address=198.18.0.42, classification=fake-ip (198.18.0.0/15)
  observation: "commonly injected by Clash/Mihomo fake-ip DNS hijacking"
- test_tcp (registry.example.org:443) -> ok, peer=198.18.0.42, 3.1ms

Recommended action: add registry.example.org to your proxy's direct/bypass list (or switch
the proxy from fake-ip to redir-host mode for this domain). Roll back by removing the entry.

Verification: curl -v https://registry.example.org/  # should reach a real CDN IP, not 198.x
```

Every value above is produced by the read-only tools and surfaced verbatim — the model never
invents a check it did not run. For the full tool trace, pass `--json`.

## Why This Project

- **Local-first** — once the model is available locally, diagnosis does not depend on an external
  model API.
- **Evidence before advice** — the model sees actual tool results before proposing a cause.
- **Read-only execution** — the runtime observes network state but never changes configuration.
- **Small-model friendly** — code controls the loop, schemas, limits, and duplicate-call handling.
- **Bilingual** — the model is instructed to answer in the user's language.

## Architecture

```text
Developer symptom or error
          │
          ▼
 MiniCPM5-1B via SGLang
          │ OpenAI-compatible tool_calls
          ▼
  Allowlisted Python tools
  DNS · TCP · HTTP · TLS · proxy env · system context
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
| `test_http` | Send one HTTP/HTTPS HEAD request |
| `inspect_tls` | Validate TLS and summarize the peer certificate |
| `inspect_proxy_environment` | Read proxy environment variables with credentials redacted |
| `system_network_context` | Report OS context and redacted proxy settings |

There is no arbitrary command or port-scanning tool.

## Agent Behavior

Two loop controls keep the small model honest and resilient:

- **Evidence-first first turn.** The first model turn is sent with
  `tool_choice="required"`, so the model must call a diagnostic tool before it is
  allowed to answer. This prevents the common small-model failure of answering
  from priors ("I don't have that tool") instead of checking. If a backend rejects
  `required`, the request is retried once with `auto`.
- **Graceful non-convergence.** If the model calls the same tool three turns in a
  row without finishing, or exhausts the turn budget, the runtime stops and
  returns a structured **partial diagnosis** — the four sections are still
  produced, but the Diagnosis section notes that the model did not converge and
  the Evidence section lists every result collected so far. The CLI therefore
  returns the gathered evidence with exit code 0 instead of failing hard. A hard
  `StepLimitError` (exit code 1) is reserved for the rare case where zero evidence
  was collected.

## Quick Start

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
minicpm5-network-doctor \
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
minicpm5-network-doctor \
  "npm install times out while fetching registry.npmjs.org; check the likely network cause"
```

Harder cases can enable MiniCPM5 thinking mode:

```bash
minicpm5-network-doctor --thinking \
  "HTTPS works in a browser but the package manager reports a certificate error"
```

Print the complete tool trace as JSON:

```bash
minicpm5-network-doctor --json "Check why https://example.com returns an error"
```

## Configuration

| Environment variable | Default |
|----------------------|---------|
| `MINICPM5_BASE_URL` | `http://127.0.0.1:30000/v1` |
| `MINICPM5_MODEL` | `openbmb/MiniCPM5-1B` |
| `MINICPM5_API_KEY` | `not-needed` |

Equivalent CLI flags are available through:

```bash
minicpm5-network-doctor --help
```

## Agent Skill

The repository includes a Codex/Claude Code-style Agent Skill:

```text
skills/minicpm5-network-doctor/
├── SKILL.md
└── agents/
    └── openai.yaml
```

For Codex, copy the folder into your personal skills directory:

```bash
cp -R skills/minicpm5-network-doctor ~/.codex/skills/
```

The Skill teaches an agent to collect the exact symptom, invoke the local doctor, preserve tool
evidence, and separate diagnosis from verification.

## Safety Boundary

- Only the six declared tools can execute.
- Hosts, ports, URLs, timeouts, and tool arguments are validated.
- Diagnostic URLs cannot contain credentials.
- Proxy credentials are redacted before results reach the model.
- Duplicate tool calls are blocked.
- Timeouts are bounded and broad scans are not supported.
- The system prompt prohibits disabling certificate verification.
- Configuration changes are suggestions only; the runtime never applies them.

## Known Limitations

MiniCPM5-1B is a 1B-parameter model. The runtime adds several guardrails to
compensate, but some failure modes are inherent to the model and cannot be fully
solved in code:

- **Tool calling is not always reliable.** The first turn forces
  `tool_choice="required"`, but some backends (notably Ollama) accept the value
  yet occasionally return a plain-text answer with no `tool_calls`. When that
  happens the model answers from priors instead of checking. This affects
  roughly a minority of runs with `Q4_K_M` and cannot be fixed without a stronger
  model.
- **Quantization degrades tool-call stability.** Heavily quantized GGUFs
  (`Q4_K_M`) emit longer chain-of-thought and are more prone to drift or
  truncation than `F16`. Prefer `F16` for the most reliable diagnosis loop. The
  `--thinking` flag helps with hard cases but also consumes more of the token
  budget on reasoning, which can itself truncate the answer.
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
  backend. Ollama works on macOS but does not propagate the
  `chat_template_kwargs.enable_thinking` body field, so thinking mode depends on
  the server's own template defaults.

When the model behaves erratically, the most effective escalation is a stronger
model (e.g. MiniCPM5 4B/8B) on a backend that reliably parses tool calls, not
more loop guardrails.

## Project Structure

```text
minicpm5-network-doctor/
├── src/minicpm5_network_doctor/
│   ├── agent.py               # Tool-calling loop
│   ├── cli.py                 # Command-line interface
│   ├── system_prompt.md       # Small-model diagnostic policy
│   └── tools.py               # Read-only diagnostic tools
├── skills/
│   └── minicpm5-network-doctor/
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
pytest
python /path/to/skill-creator/scripts/quick_validate.py \
  skills/minicpm5-network-doctor
```

Unit tests use a fake OpenAI-compatible client, so they do not download the model or require a GPU.
A real end-to-end diagnosis requires a running MiniCPM5 endpoint with working tool-call parsing.

## Credits

- [OpenBMB/MiniCPM](https://github.com/OpenBMB/MiniCPM) for MiniCPM5-1B and its tool-calling
  deployment guidance.
- [CacinieP/network-troubleshoot-skill](https://github.com/CacinieP/network-troubleshoot-skill)
  for the evidence-first network troubleshooting workflow that inspired this runtime.

## License

[MIT](LICENSE)
