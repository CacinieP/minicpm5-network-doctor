<p align="center">
  <strong>MiniCPM Network Doctor</strong>
</p>

<p align="center">
  Investigate unexpected DNS mappings, dead ports, and broken TLS with a 2B model and seven
  read-only tools, all running on your own machine.
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/CacinieP/minicpm5-network-doctor/ci.yml?branch=main&style=flat-square" alt="CI">
  <img src="https://img.shields.io/github/v/release/CacinieP/minicpm5-network-doctor?style=flat-square&color=blue" alt="Release">
  <img src="https://img.shields.io/badge/MiniCPM5-2B-blue" alt="MiniCPM5-2B">
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
[MiniCPM5-2B](https://github.com/OpenBMB/MiniCPM) model with a small set of read-only network
tools. The model decides which check is useful; deterministic Python code performs the check and
returns evidence.

The project is intentionally narrow: diagnose developer-facing DNS, TCP, HTTP, TLS, local port,
proxy environment, and package download problems without giving the model arbitrary shell access.

## See it work

A **verbatim capture from a real run** — a developer reports that `npm install` times out.
The agent resolves the host, recognizes a proxy-managed fake-IP mapping, and states what that
evidence can and cannot establish. The whole run took 38 seconds; the environment and the full
session log are in [the evidence record](docs/evidence/2026-09-18-2b-verbatim-example.md):

```text
$ minicpm-network-doctor \
    "npm install 从 registry.npmjs.org 下载时超时，请检查最可能的网络原因"

Diagnosis: The reported `npm install` timeout from `registry.npmjs.org` is most likely caused
by a local proxy (e.g. Clash/Mihomo) that is rewriting DNS and/or routing traffic through a
synthetic fake-IP, causing the real upstream to be unreachable or slow.

Evidence: `resolve_dns` for `registry.npmjs.org` returned only the fake-IP `198.18.0.38` (a
`fake-ip`/benchmarking block), and `test_tcp` to that address succeeded only because the local
proxy accepted the connection — it does not prove the real upstream is reachable. However,
`test_http` to `https://registry.npmjs.org/` returned HTTP 200 via the proxy path, so the
proxy path is active and the reported hostname resolves through it.

Recommended action: Temporarily bypass the proxy so DNS and traffic go directly to the real
`registry.npmjs.org` host. On Linux, unset `http_proxy`/`https_proxy`/`HTTP_PROXY`/`HTTPS_PROXY`
(and any `NO_PROXY`/`NO_HTTP_PROXY`/`NO_HTTPS_PROXY` variants) before running `npm install`.
On macOS/Windows, unset the corresponding environment variables. Rollback: re-set the
variables to their previous values if you need to use the proxy again.

Verification: `env | grep -i proxy` should show no proxy variables set, then re-run
`npm install` from `registry.npmjs.org` to confirm it works.
```

Note that the question was asked in Chinese and the model answered in English — the 2B model
does not always follow the answer-in-the-user's-language instruction on the reference stack;
see [Known Limitations](#known-limitations).

Every value under Evidence is produced by the read-only tools and surfaced verbatim. The model's
diagnosis may still be uncertain; it must not turn a special-use address classification into an
unsupported root-cause claim. For the full tool trace, pass `--json`.

## Why This Project

- **Local-first** — once the model is available locally, diagnosis does not depend on an external
  model API.
- **Evidence before advice** — the model sees actual tool results before proposing a cause.
- **Read-only execution** — the runtime observes network state but never changes configuration.
- **Small-model friendly** — code controls the loop, schemas, limits, and duplicate-call handling.
- **Bilingual** — the model is instructed to answer in the user's language, but the 2B model
  does not always comply (see [Known Limitations](#known-limitations)).

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
 MiniCPM5-2B via llama-server
          │ OpenAI-compatible tool_calls
          ▼
  Allowlisted Python tools
  DNS · TCP · HTTP · TLS · proxy env · hosts file · system context
          │
          ▼
 Evidence → diagnosis → one reversible recommendation → verification
```

`llama-server` (llama.cpp) is the reference backend: it serves MiniCPM5's chat template directly,
so the model's tool calls arrive as native OpenAI-compatible `tool_calls` with no parser
configuration. Any OpenAI-compatible runtime that returns native `tool_calls` also works.

## Read-Only Tools

| Tool | Purpose |
|------|---------|
| `resolve_dns` | Look up what IP addresses a hostname points to. By default it asks your own system. Add `resolver="doh:cloudflare"` (or `doh:google`, `doh:quad9`) and it also asks a public DNS-over-HTTPS server, then shows both answers side by side |
| `test_tcp` | Open one TCP connection to a host and port. Add `address=<IP>` to connect to one specific IP instead of whatever DNS returned |
| `test_http` | Send HEAD, block redirects to another host, and fall back to a bodyless ranged GET when the server rejects HEAD |
| `inspect_tls` | Check the TLS connection and read the certificate. Add `address=<IP>` to connect to one specific IP — the certificate is still checked against the hostname you reported |
| `inspect_proxy_environment` | Read your proxy settings, with passwords removed |
| `inspect_hosts_file` | Check whether one reported hostname is overridden in your local hosts file |
| `system_network_context` | Report your OS and proxy settings, with passwords removed |

There is no tool that runs arbitrary commands or scans ports.

### Comparing your local DNS with the public answer

Some proxies (Clash, Mihomo and similar, in TUN mode) answer DNS with a made-up address such as
`198.18.x.x`. The TCP connection then succeeds in a couple of milliseconds, but that handshake was
accepted by the proxy on your own machine — the real server was never contacted. So "TCP ok" there
tells you nothing about whether the website itself is reachable.

Two switches fix that, and neither of them changes any setting:

- `resolver="doh:cloudflare"` asks a public resolver over HTTPS and returns its answer next to your
  system's answer. HTTPS is used on purpose: plain DNS to a public server like `8.8.8.8` is usually
  intercepted in TUN mode, while DNS-over-HTTPS is not.
- `address=<IP>` makes `test_tcp` or `inspect_tls` connect to one specific IP. Handy right after the
  lookup above, to reach the public IP instead of the fake one.

If the two answers differ, your local DNS is rewriting the destination — say that, rather than
blaming the website.

The IP in `address` is not a new target: the hostname still has to be the one you reported, and
`address` must be an IP, never a hostname. A comparison therefore cannot turn into a scan of some
other host.

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
- **Controlled comparison without new targets.** `resolve_dns(resolver="doh:cloudflare")` shows what
  a public resolver answers next to your local answer, and `test_tcp` / `inspect_tls` accept an
  explicit IP for the reported host. The scope check still requires that host and refuses a hostname
  in `address`, so a comparison cannot drift into scanning a host you never reported.
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

### How to use it

Two processes, started separately:

| | What starts it | When |
|---|---|---|
| **Model server** (`llama-server`) | you do | before diagnosing; stays up |
| **Network Doctor** (`minicpm-network-doctor`) | you do | once per diagnosis |

**Network Doctor never starts, stops, or manages the model server.** It is only an OpenAI-compatible
client. If the endpoint is down it fails fast instead of loading anything itself.

The everyday loop:

```bash
# 1. Start the model server (separate terminal, or a background service)
llama-server -m ~/models/MiniCPM5-2B-Q4_K_M.gguf --alias minicpm5-2b --port 8080 -ngl 99 -c 65536

# 2. Confirm it answers (fast, 2.5s timeout; fails immediately rather than waiting out --timeout)
minicpm-network-doctor --check-server

# 3. Diagnose
minicpm-network-doctor "npm install times out fetching registry.npmjs.org"

# 4. Stop the model server when done to release GPU/memory
#    Ctrl+C in its terminal, or your own start/stop wrapper
```

Step 2 is worth running first: `--check-server` reports the served model list, so a mismatched
`--alias` or a server still loading surfaces in under three seconds instead of after a full turn
times out.

<details>
<summary><b>If you hand the model server to launchd / systemd</b></summary>

Keep `RunAtLoad` and `KeepAlive` **off** unless you want the model resident permanently — a
2B Q4_K_M load is not free, and an always-on job holds it across reboots. Load the unit and start it
manually when you need it.

Separately, Ollama is a common source of unexplained memory use even when you are not using it: a
request sent with `keep_alive:-1` pins a model in memory until it is explicitly unloaded. Check
`ollama ps` (or `/api/ps`) if memory climbs with no obvious process, and unload with
`keep_alive:0`.

</details>

### 1. Serve MiniCPM5-2B with llama-server

Build or install [llama.cpp](https://github.com/ggml-org/llama.cpp) and download a MiniCPM5-2B
GGUF. The `--alias` is what the CLI's default `--model minicpm5-2b` matches, so keep it unless you
also pass a different `--model`. This runs in the foreground and stays up — leave it in its own
terminal, or hand it to `launchd`/`systemd` as described above:

```bash
llama-server \
  -m ~/models/MiniCPM5-2B-Q4_K_M.gguf \
  --alias minicpm5-2b \
  --host 127.0.0.1 \
  --port 8080 \
  -ngl 99 \
  -c 65536 \
  -ctk q8_0 -ctv q8_0 \
  --parallel 1
```

The OpenAI-compatible endpoint is then `http://127.0.0.1:8080/v1`, which is what the CLI defaults
to. On macOS, a `.command` menu wrapper around this invocation keeps start/stop/log handling out
of the diagnosis loop; see [llama.cpp evidence](docs/evidence/2026-09-17-llama-cpp-2b.md) for the
verified launch configuration and measured request behavior.

<details>
<summary><b>Alternative backend: SGLang (Linux / NVIDIA GPU)</b></summary>

On an NVIDIA GPU, SGLang serves the full-precision weights through MiniCPM5's official
`minicpm5` parser:

```bash
pip install "git+https://github.com/sgl-project/sglang.git@main#subdirectory=python"

python -m sglang.launch_server \
  --model-path openbmb/MiniCPM5-2B \
  --served-model-name openbmb/MiniCPM5-2B \
  --port 30000 \
  --tool-call-parser minicpm5
```

Point Network Doctor at it:

```bash
minicpm-network-doctor \
  --base-url http://127.0.0.1:30000/v1 \
  --model openbmb/MiniCPM5-2B \
  "npm install times out fetching registry.npmjs.org"
```

</details>

> **Quantization note:** `Q4_K_M` on llama-server b10150 is the verified reference configuration
> and produced correct tool calls plus complete four-section diagnoses. Heavily quantized
> variants below that tier remain more prone to drift and truncation; if tool calls stop
> appearing, move up a quantization level before changing any runtime setting.

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

`--thinking` changes three defaults, because a 2B thinking chain is several times more expensive
than a plain turn:

| | default | why |
|---|---|---|
| `--max-tokens` | `2048` → `8192` | measured chains ran 1.1k-25.5k characters; 2048 was cut off mid-thought by `finish_reason=length` |
| `--timeout` | `180` → `600` | a full-budget thinking turn routinely exceeds 180s |
| `temperature` | `0.4` → `0.6` | thinking benefits from a little more exploration |

Override either budget directly, e.g. `--thinking --max-tokens 4096 --timeout 300`. If a thinking
turn still runs out of room, the runtime returns a partial diagnosis carrying the evidence already
collected rather than failing, and reports `model_response_truncated_by_budget` in `warnings`.

Note that even 8192 does not guarantee convergence: in testing, a multi-turn `--thinking` run still
truncated after four successful tool calls. Raising the default further would only lengthen every
turn against the timeout ceiling, so the budget stays a knob you own rather than a value the
runtime guesses at.

Print the complete tool trace as JSON:

```bash
minicpm-network-doctor --json "Check why https://example.com returns an error"
```

Check the endpoint before starting a slower model turn, or watch evidence arrive in real time:

```bash
minicpm-network-doctor --check-server
minicpm-network-doctor --progress "TLS fails for https://example.com"
```

Loopback endpoints such as a local `llama-server` at `127.0.0.1` connect directly by default
so a desktop proxy cannot intercept the model request. Remote endpoints continue to honor proxy
settings. Override either decision with `--use-model-proxy` or `--no-model-proxy`. Model API
retries default to zero to keep a slow local failure bounded; use `--max-retries` only when a
remote endpoint benefits from retries.

`--json` includes `schema_version`, `status` (`complete`, `partial`, or `error`), `targets`,
`warnings`, and the complete `tool_events` trace. Progress is written to stderr, so it is safe to
combine with machine-readable JSON on stdout.

## Configuration

| Environment variable | Default |
|----------------------|---------|
| `MINICPM_BASE_URL` | `http://127.0.0.1:8080/v1` |
| `MINICPM_MODEL` | `minicpm5-2b` |
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

MiniCPM5-2B is a small, ~2.5B-parameter model. The runtime adds several guardrails to
compensate, but some failure modes are inherent to the model and cannot be fully
solved in code:

- **Tool calling is not always reliable.** Some backends (notably Ollama) accept
  `tool_choice="required"` yet occasionally return plain text. The runtime rejects
  those ungrounded answers and retries, but a persistently non-compliant backend
  still ends with a clear no-evidence error instead of a diagnosis.
- **Thinking can exhaust its own budget.** In `--thinking` mode the model spends
  most of `--max-tokens` on chain-of-thought and can be cut off by
  `finish_reason=length` before it emits a tool call or an answer. llama.cpp
  reports that cut-off text in `reasoning_content`, leaving `content` and
  `tool_calls` empty; the runtime treats this as non-convergence and returns a
  partial diagnosis with the evidence already gathered instead of discarding it.
  Raise `--max-tokens` when you see `model_response_truncated_by_budget`.
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
- **The answer language does not follow the question.** The system prompt asks the
  model to answer in the user's language, but on the reference stack (Q4_K_M +
  llama-server b10150) three consecutive Chinese questions each got an English
  answer (see [the evidence record](docs/evidence/2026-09-18-2b-verbatim-example.md)).
  The tool evidence itself is unaffected; when a Chinese conclusion is needed, take
  the structured `--json` output and paraphrase it yourself.
- **Backend-specific behavior.** llama-server (llama.cpp) is the reference
  backend and honors both `chat_template_kwargs.enable_thinking` and an
  OpenAI-compatible `reasoning_effort` field. SGLang uses
  `chat_template_kwargs.enable_thinking`; older or different runtimes may ignore
  one or both controls, and the runtime retries without the rejected field.

When the model behaves erratically, the most effective escalation is a stronger
model — the MiniCPM5 series tops out at 2B, so step up to the previous-generation
MiniCPM4-8B or another model with reliable tool calling — on a backend that
reliably parses tool calls, not more loop guardrails.

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

- [OpenBMB/MiniCPM](https://github.com/OpenBMB/MiniCPM) for MiniCPM5-2B and its tool-calling
  deployment guidance.
- [llama.cpp](https://github.com/ggml-org/llama.cpp) for the `llama-server` runtime that serves the
  reference endpoint.
- [CacinieP/network-troubleshoot-skill](https://github.com/CacinieP/network-troubleshoot-skill)
  for the evidence-first network troubleshooting workflow that inspired this runtime.

## License

[MIT](LICENSE)
