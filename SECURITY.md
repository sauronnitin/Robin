# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Use GitHub's [private vulnerability reporting](https://github.com/sauronnitin/Robin/security/advisories/new)
(Security tab → Report a vulnerability) so the report and any discussion
stay private until a fix is ready. If that's unavailable to you, open a
regular issue asking for a private contact channel and no other detail.

Include what you'd normally include in a report: affected file/version,
reproduction steps, and impact. There's no bug bounty — this is a
personal/open-source project — but every report gets a response.

## Supported versions

This project doesn't currently maintain multiple release lines. Security
fixes land on `main`; there is no backport policy.

## Scope notes specific to this project

- **Dry-run is the default** (`DRY_RUN=True` in `.env.example`). Real job
  applications are opt-in. A vulnerability that could flip this without the
  user's action is treated as high severity.
- **Bring-your-own keys.** `GROQ_API_KEY` / `GEMINI_API_KEY` and Google
  OAuth credentials are read from a local, gitignored `.env` / `secrets/`.
  A vulnerability that could exfiltrate these (e.g. via a crafted job
  listing reaching an LLM prompt, or a path that logs them) is high
  severity — see `AGENTS.md`'s notes on treating fetched job/page content
  as untrusted data.
- **Local-first.** The dashboard binds to `127.0.0.1` and is meant to run
  on your own machine. Exposing it to a network is out of scope for this
  project's own hardening; do so at your own risk and behind your own
  auth/firewall.
