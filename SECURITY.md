# Security Policy

Forge is self-hosted and handles sensitive material — provider API keys, encrypted
secrets, auth credentials, and outbound requests to your systems. We take security issues
seriously.

## Supported versions

Forge is pre-1.0 and under active development. Security fixes land on the `main` branch and
are included in the next release. Please test against the latest `main` before reporting.

| Version | Supported |
|---|---|
| `main` (latest) | ✅ |
| older tags | ❌ (please upgrade) |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via **[GitHub Security Advisories](https://github.com/nihalashetty/Forge/security/advisories/new)**
(Repository → *Security* → *Report a vulnerability*). This keeps the report confidential
until a fix is available.

When reporting, include:

- A description of the issue and its impact.
- Steps to reproduce (a minimal proof-of-concept helps).
- Affected component/version (commit SHA if possible).

**Do not include real secrets, tokens, or customer data** in your report — redact or use
placeholders.

We aim to acknowledge reports promptly, keep you updated on remediation, and credit
reporters (unless you prefer to remain anonymous) once a fix ships.

## Built-in safeguards

Forge ships several defense-in-depth controls; when reporting, note if your finding
bypasses one:

- **SSRF guard** on every outbound call (tools, webhooks, fetch, crawl) — private,
  loopback, and cloud-metadata addresses are blocked by default.
- **Encrypted, reference-only secrets** (`secret://…`) via a Fernet master key; secret
  values are write-only and never returned.
- **Production hardening guard** that refuses to boot with default secrets, auth disabled,
  a non-durable checkpointer, or the SSRF guard off.
- **Multi-tenant isolation** with query-level scoping and optional Postgres row-level
  security.
- **Sandboxed code tools** (RestrictedPython) and origin-locked embed widgets.

## Scope

In scope: the Forge API, web console, engine, tools/auth subsystems, and the container
stack. Out of scope: vulnerabilities in third-party dependencies (report those upstream;
tell us if Forge's usage makes them exploitable), and issues requiring a
already-compromised host or admin credentials.
