# Real-model E2E benchmark plan

Target milestone: **v0.4.0**. Status: **planned; no benchmark score is claimed yet**.

The unit suite verifies deterministic tools, safety boundaries, the CLI, and the agent loop with a
fake OpenAI-compatible client. Manual model runs can prove that an endpoint emits tool calls, but
they cannot establish diagnostic accuracy or stability. This plan defines the evidence required
before the project makes either claim.

The current one-run Ollama observation is retained separately as a
[pre-release smoke record](evidence/2026-09-02-ollama-f16-smoke.md). Its inconclusive model result
is not included in any aggregate metric.

## Evaluation matrix

Each backend configuration runs every scenario five times. The initial suite contains 20 fixed,
labelled scenarios:

| Area | Scenarios |
|---|---|
| DNS and mappings | healthy public DNS; NXDOMAIN; healthy fake-IP proxy path; failing request on a fake-IP path; hosts-file override; expected private split-horizon result |
| TCP and HTTP | connection refused; bounded connection timeout; HTTP 200; HEAD 405 with ranged-GET fallback; same-host redirect; blocked cross-host redirect; HTTP 401 |
| TLS | valid certificate; expired certificate; hostname mismatch; self-signed certificate |
| Runtime and model | redacted authenticated proxy; model endpoint unavailable; repeated/non-tool model output |

Fixtures should be controlled containers or local test services. Public internet services may be
used only as supplemental observations, never as the labelled ground truth.

## Backend configurations

Record exact hardware, operating system, model artifact and hash, quantization, runtime version,
parser version, thinking mode, sampling parameters, and repository commit for every run.

Initial candidates:

- SGLang with MiniCPM5-1B BF16 and the native `minicpm5` parser on a supported NVIDIA system.
- Ollama with MiniCPM5-1B F16 on Apple Silicon.
- An available AIPC backend with Q8 GGUF.
- Q4_K_M as an explicitly lower-confidence comparison, not a recommended baseline.

A configuration remains **untested** until its raw run artifacts are stored; hardware suitability
must not be inferred from documentation alone.

## Metrics

For each scenario and configuration, calculate:

- **Tool-call success rate:** at least one valid tool call when evidence is required.
- **Target accuracy:** calls stay on the labelled host, URL, and port.
- **Evidence fidelity:** every factual value in the answer is present in tool output.
- **Root-cause accuracy:** the diagnosis matches the labelled cause or correctly says the cause is
  unconfirmed when the tools cannot distinguish it.
- **Unsupported causal-claim rate:** especially fake-IP, proxy, DNS, and TLS attribution errors.
- **Convergence rate:** a complete grounded answer arrives within the configured budgets.
- **Latency:** wall time to first evidence and to final result.

## Proposed Beta gates

The reference configuration must satisfy all gates over at least 100 runs (20 scenarios × 5):

- Zero out-of-scope network executions and zero credential leaks.
- At least 98% evidence fidelity.
- At least 95% target accuracy and tool-call success.
- At least 90% convergence.
- At least 85% root-cause accuracy on decidable scenarios.
- At most 2% unsupported causal claims, with zero unsupported claims in the healthy fake-IP case.

These thresholds are proposals, not current results.

## Evidence artifacts

Store the following for every benchmark revision:

- A versioned manifest describing scenarios, expected outcomes, configurations, and hashes.
- Append-only JSONL with prompts, model responses, `tool_events`, timings, and final status.
- Human labels and adjudication notes separated from model output.
- A summary table generated only from the retained raw records.
- Redaction checks confirming that no credentials, unrelated hosts-file entries, or private user
  data enter the artifact set.

Failures stay in the dataset. Do not discard or rerun an unsuccessful sample without retaining the
original record and a reason for the additional run.

## 中文摘要

v0.4.0 计划使用 20 个固定故障场景，并对每种后端配置重复运行 5 次。核心指标包括工具调用
成功率、目标准确率、证据忠实率、病因准确率、无依据因果结论率、收敛率和延迟。在原始 JSONL、
环境清单、模型与代码哈希公开保存之前，项目只应声明“链路可运行”，不应声明诊断可靠性。
