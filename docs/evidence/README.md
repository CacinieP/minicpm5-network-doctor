# Evidence records

Every file in this directory is a **dated record of one real run**. It is evidence about the
runtime, not a benchmark, and it is never allowed to stand in for the controlled evaluation
defined in [`../benchmark-plan.md`](../benchmark-plan.md).

A record is only useful if another person can tell what was actually observed. Files here are
therefore written against the checklist below; a field that was not captured is written down as
*not captured*, never guessed or reconstructed afterwards.

## Required fields

1. **Exact version and source state** — package version plus the commit the run was made from.
2. **Environment** — timestamp in UTC, host platform, backend name and version, model name,
   quantization artifact and digest, and Python version.
3. **Model residency** — whether the model was already loaded (warm) or had to be read from disk
   for the first request (cold). A cold first load can exceed any patience budget and is not a
   property of the agent.
4. **Complete invocation** — the full command line: every flag and value, including
   `--thinking`, `--timeout`, `--max-tokens`, `--max-steps`, `--max-tool-calls`, and the model
   name. Defaults change between releases, so an invocation without its flags is not reproducible.
5. **Backend parameters in effect** — context length, `keep_alive`/`-to`, parallelism, and any
   chat-template flags the run depended on.
6. **Per-check outcome** — one row per check, with the pass/fail outcome, the wall-clock time
   observed, and the boundary of what the check can prove.
7. **Interruption and waits** — if a run was interrupted, the wall-clock time actually waited
   before the interruption. Without it, an "inconclusive" row cannot be separated from
   "the wait was shorter than the work needed".
8. **Explicit non-claims** — a closing paragraph stating what the run does *not* establish.

## Reporting an inconclusive result

`Inconclusive` is a legitimate outcome, but only when the record distinguishes the candidate
causes. Before writing it, check whether the missing information is:

- the model produced no tool call, or
- the budget (tokens, timeout, patience) ran out first.

If the record cannot tell those apart, say so in the row itself and treat the gap as a follow-up
issue rather than a footnote in a passing release.

## Known gaps in existing records

- `2026-09-02-ollama-f16-smoke.md` — recorded before this checklist existed; see its addendum for
  the fields that were not captured and therefore cannot be reconstructed.
