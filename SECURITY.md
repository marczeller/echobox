# Security Policy

## Reporting a Vulnerability

If you find a security issue in Echobox, please report it privately:

- Use [GitHub's private vulnerability reporting](https://github.com/marczeller/echobox/security/advisories/new)
- Do NOT open a public GitHub issue for security vulnerabilities

## Scope

Echobox processes call recordings and transcripts locally. Security-relevant areas:

- **Config files** may contain passwords (`publish.password`) — `.gitignore` excludes `config/echobox.yaml`
- **Shell command execution** — context source commands are user-configured and executed via `bash -c`
- **Vercel gate** — the password-gated report deployment uses HMAC tokens
- **LLM enrichment** — transcript content is sent to the configured LLM endpoint (local by default)

## Design Decisions

- Enrichment runs against a local LLM server by default (no cloud API)
- Web context lookup (DuckDuckGo) is disabled by default (`web.enabled: false`)
- Claude CLI report generation is opt-in (`publish.engine: claude`)
- The Vercel gate refuses deployment with default passwords
