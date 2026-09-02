# Ollama F16 pre-release smoke record — 2026-09-02

This is a single pre-release observation for version 0.3.1. It is **not** a benchmark result and
must not be used to claim diagnostic accuracy or stability. The controlled evaluation required
for those claims is defined in [`../benchmark-plan.md`](../benchmark-plan.md).

## Environment

- Timestamp recorded after the run: `2026-09-02T08:45:52Z`
- Host platform: `Darwin 25.6.0 arm64`
- Ollama: `0.32.15`
- Model: `hf.co/openbmb/MiniCPM5-1B-GGUF:F16`
- Model digest: `559931484eba0ef588688f69d6811c5cf2a50e3a6aeef4182fa3c4fff2201d18`
- Model metadata: MiniCPM5-1B, 1.08B parameters, F16 artifact, 131072 context length
- Source state: version 0.3.1 changes based on commit `5f51446`

## Observations

| Check | Outcome | Evidence boundary |
|---|---|---|
| Model preflight | Passed | `/v1/models` was reachable through a direct loopback connection and listed the requested F16 model. |
| Real DNS tool | Passed | `registry.npmjs.org` resolved to `198.18.1.31`, classified as `fake-ip (198.18.0.0/15)`. The 0.3.1 observation said this identifies a proxy-managed path and does not establish failure or causality. |
| Full F16 diagnosis | Inconclusive | No tool event was observed before manual interruption. Because the run was manually interrupted and no complete response exists, it is not scored as a diagnosis result. |
| Five-second timeout probe | Passed | The CLI returned `{"schema_version": 1, "status": "error", "error": "MiniCPM5 request failed: Request timed out."}` in 6.56 seconds wall time. |

The smoke run confirms that the endpoint, neutral deterministic fake-IP observation, and bounded
timeout error path operate on the real local stack. It does **not** prove that the model reliably
selects tools, converges, or diagnoses the correct cause. The F16 model was unloaded after the
check; no model files or system settings were changed.
