# Evidence records

Each file here is a **record of one real run on one machine**. It is evidence about the runtime, not
a benchmark, and it can never replace the controlled evaluation described in
[`../benchmark-plan.md`](../benchmark-plan.md).

A record is only worth keeping if someone else can tell what actually happened. If a detail was not
written down at the time, it is recorded as *not captured* — never guessed afterwards.

## What every record must contain

1. **Version and commit** — the package version and the commit the run was made from.
2. **Environment** — UTC timestamp, machine and OS, backend name and version, model name,
   quantization file and its digest, Python version.
3. **Was the model already loaded?** A model read from disk on its first request can take longer
   than anyone is willing to wait, and that is not the agent's fault. A record that does not say
   which case it was cannot separate "the model did nothing" from "the wait was too short".
4. **The exact command** — every flag and value, including `--thinking`, `--timeout`,
   `--max-tokens`, `--max-steps`, `--max-tool-calls`, and the model name. Defaults change between
   releases, so a command line without its flags cannot be reproduced.
5. **Backend settings in effect** — context length, `keep_alive`, parallelism, and any chat-template
   flags the run depended on.
6. **One row per check** — what happened, how long it took, and what the check can and cannot prove.
7. **Any interruption** — if a run was stopped by hand, how long you actually waited first.
8. **What it does not prove** — a short closing list of the claims the run does not support.

## Writing down an "inconclusive" result

"Inconclusive" is an honest outcome, but only if the record rules out the boring explanation. Before
writing it, check which of these you can already exclude:

- the model never emitted a tool call, or
- the budget ran out first (tokens, timeout, or your own patience).

If the record cannot tell those apart, say so in the row itself and open a follow-up issue for it
instead of leaving it as a footnote in a passing release.

## Known gaps in existing records

- `2026-09-02-ollama-f16-smoke.md` — written before this checklist existed. Its addendum lists the
  fields that were never captured and therefore cannot be recovered.
