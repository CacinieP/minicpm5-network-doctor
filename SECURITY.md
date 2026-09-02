# Security Policy

## Supported version

Security fixes target the latest released minor version.

## Safety model

MiniCPM Network Doctor is designed to observe a narrowly scoped network symptom without granting
the model shell access or configuration write access.

- Only the seven registered Python handlers can execute.
- Every network tool call is restricted to a hostname or IP explicitly present in the original
  user report.
- Ports, URLs, timeouts, credentials, per-turn calls, and diagnosis-wide calls are bounded or
  validated before execution.
- Proxy credentials are redacted. Diagnostic URLs cannot contain credentials.
- HTTP diagnostics block redirects to a different host, so a reported target cannot redirect the
  runtime into an unrelated network location.
- Model-server preflight only follows same-origin redirects and loopback model endpoints bypass
  environment/system proxies by default.
- Hosts-file inspection returns only entries matching the requested host.
- The runtime does not change DNS, proxy, certificate, route, firewall, or application settings.

This boundary does not make arbitrary diagnostic targets harmless. Only diagnose systems you are
authorized to access, and do not place secrets in prompts.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository when available. Include the
affected version, impact, reproduction steps, and any suggested mitigation. Do not open a public
issue containing credentials, private hostnames, or exploit details before a fix is available.
