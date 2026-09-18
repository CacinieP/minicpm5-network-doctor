# MiniCPM5-2B Q4_K_M — README example grounding and answer-language check

Date: **2026-09-18** (UTC+8). Status: **real-run capture; not a benchmark score.**

This record backs two README changes made the same day: the "See it work" example was
replaced with a verbatim capture from run 4 below, and the bilingual claim was softened
after runs 1, 4, and 5 all answered English to Chinese questions. It does **not** establish
diagnostic accuracy, run-to-run stability, or any win/loss against other models — that is
what [the benchmark plan](../benchmark-plan.md) covers.

## Version and environment

| Item | Value |
|---|---|
| Package | v0.5.1, commit `b79cddb` (working tree carried docs-only local edits, no code changes) |
| Host | macOS, Apple Silicon (Darwin arm64), 8 GB unified memory |
| Runtime | `/opt/homebrew/bin/llama-server` b10150 (`dee2a846b`), full GPU offload `-ngl 99` |
| Model artifact | `~/models/MiniCPM5-2B-Q4_K_M.gguf` (symlink into an Ollama blob store) |
| Artifact digest | sha256 `ec2d5801640099e97d8d7e8003ad4d81f336e757811f03a26173dddf386602fd` (matches the Ollama blob name) |
| Endpoint | `http://127.0.0.1:8080/v1`, served id `minicpm5-2b`, connection direct (loopback proxy bypass active) |
| Python | 3.13, fresh `.venv`, `pip install -e .` |

**Model loading:** `llama-server` loads the model during startup, and `llamactl start`
gates readiness on `/health`, so every run below hit a fully loaded model. The server was
started twice (before run 1 and before run 4); no run raced a cold load.

**Wall-clock times were not captured** at run time; durations below are from `time` and are
exact. Anchor: the server log's last write before teardown was 2026-09-18 14:36:50 +0800,
which is the end of run 5.

## Commands, with every default in effect

All runs used the plain defaults of v0.5.1: `--max-steps 6 --max-tool-calls 12
--max-tokens 2048 --timeout 180 --max-retries 0`, temperature 0.4, no `--thinking`.
Only the prompt and `--json` varied:

```bash
# runs 1, 4
minicpm-network-doctor "npm install 从 registry.npmjs.org 下载时超时，请检查最可能的网络原因"
# run 2
minicpm-network-doctor --json "浏览器能访问 registry.npmjs.org，但包管理器报告证书错误"
# run 3
minicpm-network-doctor --json "检查 github.com 的 443 端口连不上的原因"
# run 5
minicpm-network-doctor "浏览器打开 https://example.com 一切正常，但 curl 访问时报证书错误，帮我查一下原因"
```

## Backend settings in effect

`-c 65536 -ctk q8_0 -ctv q8_0 --parallel 1 --alias minicpm5-2b`, launched via
`~/bin/llamactl` (launchd label `com.caciniep.llama-server-minicpm5`). `--check-server`
reported `minicpm5-2b`, `n_ctx=65536`, direct connection.

## One row per run

| # | Prompt language | Answer language | Duration | Tools called | Outcome |
|---|---|---|---|---|---|
| 1 | zh | en | 45.3 s | `resolve_dns`, `test_tcp`, `test_http` | four-section answer, complete |
| 2 | zh | en | not captured | `resolve_dns`, `test_tcp`, `test_http` | `--json`: `status=complete`, `schema_version=1`, targets `[registry.npmjs.org]`, no warnings |
| 3 | zh | en | not captured | `resolve_dns`, `test_tcp`, `test_http` | `--json`: `status=complete`, targets `[github.com]`, no warnings |
| 4 | zh | en | 37.9 s | `resolve_dns`, `test_tcp`, `test_http` | four-section answer, complete; **this is the run quoted verbatim in both READMEs** |
| 5 | zh | en | 33.7 s | `resolve_dns`, `test_tcp`, `test_http`, `inspect_tls`, `inspect_proxy_environment` | four-section answer, complete |

What the checks proved on this host: `registry.npmjs.org` resolved only to `198.18.0.38`
(fake-IP, 198.18.0.0/15) and `example.com` to `198.18.0.40` — the machine's Clash-style
TUN proxy was managing DNS; TCP to those addresses succeeded and HTTPS returned 200 through
`used_environment_proxy: true`. The runs correctly declined to treat the fake-IP mapping
alone as proof of upstream failure. Run 5's Diagnosis paragraph also claimed the fake-IP
path "cannot establish a valid TLS connection" while its own `inspect_tls` result succeeded
against the proxy path — recorded as an observed 2B wording wobble, one reason run 4 was
chosen for the README instead.

## Interruptions

None. The server was stopped by hand after run 5 as planned teardown (`llamactl stop`),
not mid-run.

## What this does not prove

- No accuracy or stability claim: five converging runs on one host is a smoke result,
  not the planned benchmark.
- The answer-language failure is n=3 on one host, one quant, one backend. It shows the
  old "the model answers in your language" README claim was not reliable here; it does
  not measure how often the model complies overall.
- Timing under other quants, context lengths, or thinking mode was not measured.
