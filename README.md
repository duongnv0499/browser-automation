# Browser Automation

Async Python browser control for agent clients, with explicit native Chrome attachment or an isolated browser, revision-bound DOM observations, and independently verified model-driven tasks.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
```

Playwright's browser install is needed for isolated Chromium. Attaching to an already running, opted-in Chrome uses that browser instead. Native attachment must be explicitly approved; this project never enables debugging, restarts your browser, or silently falls back to an isolated profile.

Provider credentials are configured through environment variables. Keep keys outside Git and use `uv run --env-file .env ...` when loading a local environment file. The supplied `.env.example` is an editable reference, not real credentials.

## Working agreement

See [AGENTS.md](AGENTS.md), [worker instructions](agents/worker-instructions.md), and [durable workstreams](agents/workstreams.json). Preserve the user's `doc.md`. Workers implement scoped features; the parent reviews; Harness serializes feature commits and pushes. Verification is coordinated after implementation completes.

## Evidence and limitations

No matched benchmark against Jev or Codex has been run, so no comparative speed or reliability claim is made. Browser-only timings, deterministic local-agent timings, and live-provider timings must be reported separately. Live provider success cannot be inferred from mocked transport tests. See [research](docs/research.md) for dated primary sources and unresolved contracts.
