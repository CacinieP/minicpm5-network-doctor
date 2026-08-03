---
name: minicpm-network-doctor
description: Operate the MiniCPM5 Network Doctor local diagnostic agent for evidence-based, read-only investigation of DNS failures, HTTP errors, TLS certificate problems, unreachable hosts, closed ports, proxy configuration, package download failures, and network timeouts. Use when a user wants to diagnose a development network error with a locally served MiniCPM5 model or validate that the Network Doctor CLI is configured correctly.
---

# MiniCPM5 Network Doctor

Use the local MiniCPM5 agent to collect a small amount of relevant evidence, explain the likely cause, recommend one reversible action, and state how to verify it.

## Workflow

1. Capture the failed operation, exact error, target host, and whether the problem affects one target or many.
2. Confirm that the local OpenAI-compatible MiniCPM5 endpoint is running with tool-call parsing enabled.
3. Run:

   ```bash
   minicpm-network-doctor "<exact symptom and target>"
   ```

4. Preserve the tool evidence in the result. Do not replace observed values with assumptions.
5. Present the diagnosis, evidence, recommended action, and verification command separately.
6. If evidence is inconclusive, request one additional targeted observation instead of proposing broad configuration changes.

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
- Explain the scope and rollback for every suggested configuration change.
- Match the user's language.
