# Security Policy

## Supported Versions

Only the latest released version of `ytm-player` receives security fixes.

## Reporting a Vulnerability

If you've found a security vulnerability — credential leakage, IPC abuse,
arbitrary code execution via crafted YouTube Music API responses, or
similar — please **do not** open a public GitHub issue.

Instead, use GitHub's private vulnerability reporting:
**https://github.com/peternaame-boop/ytm-player/security/advisories/new**

I'll respond within 7 days, confirm or dispute the issue, and coordinate
disclosure.

## Scope

In scope:
- The `ytm-player` Python package
- The CLI entry points (`ytm`, `python -m ytm_player`)
- IPC protocol (Unix socket / TCP fallback on Windows)
- Auth file handling (cookies, OAuth tokens)

Out of scope:
- Vulnerabilities in upstream dependencies (ytmusicapi, yt-dlp, mpv, etc.)
  — please report those to the respective projects
- Issues that require local root or other privileged access
