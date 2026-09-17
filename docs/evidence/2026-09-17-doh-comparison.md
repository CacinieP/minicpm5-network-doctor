# Read-only controlled comparison on a TUN fake-IP stack — 2026-09-17

A single observation of the v0.5.0 comparison tools on a machine whose system resolver is inside a
Clash/Mihomo TUN fake-ip setup. This is **not** a benchmark and makes no claim about diagnostic
accuracy or stability. It exists to show that the new tools produce real, comparable values on a
stack where the two resolution paths genuinely disagree.

Written against the required-field checklist in [`README.md`](README.md).

## Environment

- Timestamp: `2026-09-17T15:48:22Z` (checks run within the same minute)
- Host platform: `Darwin 25.6.0 arm64`
- Python: `3.14.7` (project venv `.venv-test`)
- Source state: unreleased `main` at commit `1e91b1c`, package version `0.5.0`
- Local proxy: Clash-style TUN with fake-ip; `HTTPS_PROXY`/`HTTP_PROXY` set to `http://127.0.0.1:7890`
- No model server was running, so no model turn was exercised: only the deterministic tools.

## Observations

| Check | Outcome | Evidence boundary |
|---|---|---|
| `resolve_dns(example.com)` | System path returned `198.18.0.6` | Classified `fake-ip (198.18.0.0/15)`; identifies a proxy-managed DNS path only. |
| `resolve_dns(example.com, resolver="doh:cloudflare")` | DoH path returned `104.20.23.154`, `172.66.147.243`, `2606:4700:10::ac42:93f3`, `2606:4700:10::6814:179a`, all `public` | Same call also returned the system answer and the note "The two resolution paths disagree … The local DNS answer is therefore not the public DNS answer". |
| `resolve_dns(registry.npmjs.org, resolver="doh:cloudflare")` | DoH path returned eight `104.16.0.0/12` addresses; system answer was `198.18.0.52` | Same disagreement; no claim about which path the application used. |
| `test_tcp(example.com, 443)` | `ok`, `peer_address=198.18.0.6`, `peer_classification=fake-ip (198.18.0.0/15)`, `6.5 ms` | The attached observation states this only proves the local proxy accepted the connection. The 6.5 ms handshake is consistent with a local TUN accept, not an upstream round trip. |
| `test_tcp(example.com, 443, address="104.20.23.154")` | `ok`, `peer_address=104.20.23.154`, `peer_classification=public`, `8.1 ms` | Explicit-address path; says the address is reachable, nothing about DNS. |
| `inspect_tls(example.com, address="104.20.23.154")` | `ok`, `TLSv1.3`, `subject=commonName=example.com`, issuer `SSL Corporation / Cloudflare TLS Issuing ECC CA 3`, `40` days remaining, `1903.2 ms` | Connected to the DoH-reported address while validating `example.com` for SNI and certificate, so the certificate matches the reported host, not merely the address. |

## What this does not establish

- Nothing about model behaviour: no model turn ran, so no tool-selection or convergence claim.
- No repetition and no scenario suite: one run per check, so no stability or accuracy claim.
- The disagreement shows the local DNS answer is not the public answer; it does **not** show that
  the proxy caused any particular application failure.
- The DoH request itself went through the configured local proxy, so this is a comparison of
  resolution paths, not a bypass of the proxy.
