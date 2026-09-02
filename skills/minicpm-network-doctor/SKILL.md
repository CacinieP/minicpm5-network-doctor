---
name: minicpm-network-doctor
description: Operate MiniCPM Network Doctor for evidence-based, read-only investigation of DNS, HTTP, TLS, port, proxy, hosts-file, package-download, and timeout failures. Use when diagnosing a development network error with a local OpenAI-compatible MiniCPM endpoint or validating the CLI and model-server connection.
---

# MiniCPM Network Doctor

Use the local MiniCPM5 agent to collect a small amount of relevant evidence, explain the likely cause, recommend one reversible action, and state how to verify it.

## Workflow

1. Capture the failed operation, exact error, and explicit target hostname, IP, or HTTP(S) URL. The
   runtime rejects prompts without a target and network calls outside the original target set.
2. When setup is uncertain, check the local OpenAI-compatible endpoint and its served model list:

   ```bash
   minicpm-network-doctor --check-server
   ```

   Loopback endpoints connect directly by default to avoid desktop-proxy interference. Only use
   `--use-model-proxy` when the configured local server is intentionally reached through a proxy;
   use `--no-model-proxy` to force a direct remote connection.

3. Run the focused diagnosis. Use `--progress` when live evidence on stderr is useful, and `--json`
   when the caller needs the stable status, targets, warnings, and complete tool trace:

   ```bash
   minicpm-network-doctor "<exact symptom and target>"
   ```

4. Preserve the tool evidence in the result. Treat `status: partial` as usable evidence with an
   inconclusive model conclusion, not as a fully supported diagnosis.
5. Present the diagnosis, evidence, one reversible recommended action, and verification command
   separately.
6. If evidence is inconclusive, request one additional targeted observation instead of proposing
   broad configuration changes.

## Input guidance

Include concrete details whenever available:

- Exact error text
- URL or hostname
- Port and protocol
- Original command or application
- Operating system
- Whether another target works from the same machine

Prefer a focused request such as:

```text
npm install times out while fetching registry.npmjs.org on macOS.
DNS works in the browser, but the terminal command fails.
```

## Guardrails

- Keep diagnostics read-only.
- Never claim a check ran unless its tool result is present.
- Never expose credentials contained in environment variables or URLs.
- Never recommend disabling certificate verification as a fix.
- Avoid broad scans. Test only the host and port relevant to the reported failure.
- Do not work around `target_out_of_scope`; add a target only when the user actually reported it.
- A plain model answer without tool evidence is not a diagnosis. Preserve the CLI's no-evidence
  error and recommend checking backend tool-call parsing.
- Explain the scope and rollback for every suggested configuration change.
- Match the user's language.
