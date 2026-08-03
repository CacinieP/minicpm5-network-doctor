<p align="center">
  <strong>MiniCPM5 Network Doctor</strong>
</p>

<p align="center">
  A small, local-first network diagnostic agent powered by MiniCPM5 tool calling.
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/CacinieP/minicpm5-network-doctor/ci.yml?branch=main&style=flat-square" alt="CI">
  <img src="https://img.shields.io/badge/MiniCPM5-1B-blue" alt="MiniCPM5-1B">
  <img src="https://img.shields.io/badge/Python-3.10%2B-green" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT License">
</p>

<p align="center">
  <a href="./README_CN.md">中文文档</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#safety-boundary">Safety</a> ·
  <a href="#development">Development</a>
</p>

---

MiniCPM5 Network Doctor combines a locally served
[MiniCPM5-1B](https://github.com/OpenBMB/MiniCPM) model with a small set of read-only network
tools. The model decides which check is useful; deterministic Python code performs the check and
returns evidence.

The project is intentionally narrow: diagnose developer-facing DNS, TCP, HTTP, TLS, local port,
proxy environment, and package download problems without giving the model arbitrary shell access.

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
