# Browser Automation

Guarded browser control and a multimodal Luna Decisions agent for Python, Codex, Claude Code, Hermes, and OMP. Use your opted-in, logged-in Chrome **or explicitly choose** an isolated browser.

## Strengths

- Revision-bound element observations with compatible operations, stale/covered-target checks, and real Playwright input—not model-generated selectors or JavaScript.
- DOM text plus optional PNG screenshots, including visual-target observations for interfaces that cannot be described completely by HTML controls.
- OpenRouter alpha Decisions as the primary provider, with a distinct OpenAI Decisions adapter. One typed choice selects a compatible operation/target pair; field text uses a separate structured-output call.
- Persistent JSONL CLI, default MCP stdio and optional authenticated Streamable HTTP; independent verification before a model-selected DONE counts as success.
- Explicit native-profile consent, owned-tab lifecycle, and approval-bound consequential actions/file transfers.
- Structured DOM coverage/page state, opt-in provider visual summaries and incremental bounded-run progress; recovery remains host-policy/approval controlled.
- Bounded owned-tab navigation and scoped passive HTTP/application-WebSocket diagnostics with safe metadata defaults.

These are design capabilities, not claims of better performance than Jev or Codex. Supported surfaces, limitations, and verification evidence are documented below.

For agent-native diagnosis, see [navigation/coverage and frame safety](docs/browser.md), [visual provenance/recovery authority](docs/providers.md#page-state-and-opt-in-visual-interpretation), and [monitor/progress tools](docs/integrations.md#bounded-navigation-and-semantic-observation). These capabilities do not establish a live YouTube/Facebook/TikTok repair, personal macOS/native-profile proof or live-model quality; dated exercised evidence and limits are in [research](docs/research.md).

## Let your coding agent install

Paste this into Codex (with permission to install local tools):

> Install https://github.com/duongnv0499/browser-automation for me. Read its install.md and perform the installation, not just give me commands. Set up the local stdio MCP server and the browser-automation skill, preserve existing configuration and secrets, and verify real tool discovery and doctor. Do not attach my Chrome or make paid model calls without my explicit consent.

For HTTP instead, add: “Use Streamable HTTP; connect to my existing server, or set up the server on the browser host only if I ask.” The agent must distinguish server setup from client registration and provision bearer credentials privately.

[install.md](install.md) is the complete self-install runbook: stable paths, dependencies, idempotent MCP/skill setup, protocol checks, and readiness reporting. Local stdio needs no running daemon. A fresh/reloaded Codex session may be needed to expose newly registered tools; installation alone does not grant browser or action consent.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The manual example below assumes a new checkout; for an existing install, preserve local changes and follow [the idempotent runbook](install.md).

```bash
git clone https://github.com/duongnv0499/browser-automation.git
cd browser-automation
uv sync --locked
# Create only when absent; retain existing credentials and broken-link conflicts.
if [ ! -e .env ] && [ ! -L .env ]; then
  (umask 077; set -C; cat .env.example > .env)
fi
# Edit .env privately: set a provider key only if using a model.
uv run --env-file .env browser-agent doctor
```

Only if explicitly choosing **isolated Chromium**, also run:

```bash
uv run playwright install chromium
# On a minimal Linux host, install OS browser dependencies if required:
# uv run playwright install --with-deps chromium
```

Native attachment uses your running Chrome instead of Playwright's Chromium download. The package does not automatically load `.env`; use `uv run --env-file .env ...`. Direct browser observation/control does not require a model key. Luna model access and billing depend on your provider account; field text generation is a separate paid request.

## Native, logged-in Chrome (primary workflow)

1. In a Chrome version that exposes it, open `chrome://inspect/#remote-debugging` and explicitly enable **Allow remote debugging for this browser instance**. This grants powerful browser-profile access; keep the endpoint local.
2. Keep that browser running. Approve Chrome's own connection prompt when it appears.
3. Set host consent and start the persistent worker:

```bash
BROWSER_NATIVE_CONSENT=1 uv run --env-file .env browser-agent serve
```

Send one JSON object per line on stdin:

```json
{"id":1,"command":"connect_default","arguments":{}}
```

The response supplies `session_id` and existing tabs. Retain those IDs, then send:

```json
{"id":2,"command":"observe","arguments":{"session_id":"RETURNED_SESSION_ID","tab_id":"RETURNED_TAB_ID","screenshot":true}}
```

Do not literally use the uppercase placeholders: substitute returned IDs. Set `BROWSER_NATIVE_PROFILE_DIRECTORY` to the **user-data directory** containing `Local State` and `DevToolsActivePort` if the profile is not at a standard discovery location; it is not the `Default`/`Profile 1` subdirectory.

Discovery reads opt-in state; it never enables settings, restarts Chrome, or launches an isolated substitute. A missing/disabled native session is an explicit error. Existing tabs and the attached browser survive disconnect; only session-owned tabs may be explicitly closed. Chrome's Codex/ChatGPT extension protocol is not used or claimed to be implemented.

For an already approved explicit loopback CDP endpoint:

```bash
uv run browser-agent connect --endpoint http://127.0.0.1:9222
```

That command retains its session until stdin EOF. See [browser behavior and limits](docs/browser.md) and [external tools/approvals](docs/integrations.md).

For direct native discovery use `uv run browser-agent connect --consent` (optionally `--profile-dir /absolute/user-data-directory`). A model-driven native task uses `uv run --env-file .env browser-agent run 'YOUR GOAL' --consent --url https://example.com`. `run` defaults to native discovery; `--isolated` is required to select an isolated browser.

## Explicit isolated browser

```bash
# Persistent visible browser, controlled through subsequent JSON lines:
uv run browser-agent launch
# Explicit headless browser is useful for server-side fixtures:
uv run browser-agent launch --headless
# Paid, bounded agent task in an explicitly chosen isolated browser:
uv run --env-file .env browser-agent run \
  'Open the Wikipedia article about browser automation; stop when its heading is visible.' \
  --isolated --url https://en.wikipedia.org --provider openrouter --max-steps 20
```

`run --endpoint http://127.0.0.1:9222` instead uses an explicit attached session. Account sign-in, CAPTCHA, and site permission prompts remain user responsibilities. No stealth/detection bypass is provided. A blocked or unapproved action is not successful completion.

## Python

```python
import asyncio
from browser_automation.browser import BrowserSession

async def main():
    # User has opted in within Chrome and authorizes this attachment.
    async with await BrowserSession.connect_default(consent=True) as browser:
        tabs = await browser.tabs()
        observation = await browser.observe(tabs[0]["id"], screenshot=True)
        print(observation["title"], observation["text"])
        # For input, select a returned compatible element ID and include
        # observation_id=observation["id"]; never create model selectors.

asyncio.run(main())
```

For isolation use `await BrowserSession.launch(headless=False)` explicitly. See [architecture](docs/architecture.md) and [provider configuration](docs/providers.md).

## Run MCP once, connect compatible agents

On the **browser host**, install `uv sync --locked --extra http`, securely configure `BROWSER_MCP_TOKEN`, then start:

```sh
uv run browser-agent-mcp --transport streamable-http --host 127.0.0.1 --port 8767
```

Compatible Streamable HTTP/bearer clients connect to `http://127.0.0.1:8767/mcp`, or a protected HTTPS endpoint/tunnel for remote clients. The browser stays on the server host; remote MCP is not remote CDP or automatic attachment to the client's Chrome. Native consent and host-bound action/file approvals are unchanged. Distinct principals need distinct configured tokens; sharing a bearer identity deliberately shares its modern-protocol browser scope.

See [secure token generation, Codex/Claude configuration, SDK client and TLS proxy setup](docs/http.md). Default stdio and the existing OMP stdio bridge remain available; not every agent/product supports the required protocol/authentication. Provider API keys for Luna `run` are separate from MCP bearer credentials.

An optional [professional portable skill](skills/browser-automation/SKILL.md) helps agents choose bounded goals, use Luna decision-first, retain session IDs, budget screenshots/context, pause for host approvals and independently verify outcomes. [Install/discovery instructions](docs/integrations.md#portable-professional-skill) cover Codex, Claude Code and Hermes. Skill installation is not required for tool access and grants no extra authority.

## Agent clients (MCP stdio)

Use an absolute checkout path so client working directories do not matter. Browser-only tools need no API key. To expose native sessions to a client, set `BROWSER_NATIVE_CONSENT=1` in the trusted host/server environment **after reviewing the access**, then call `connect_default`. Approval records and file directories are host policy, not model-controlled parameters.

### Codex

Use the [self-install runbook](install.md) for safe, idempotent registration and skill discovery. For an inspected unused name and discovered absolute paths:

```bash
codex mcp add browser-automation -- /ABSOLUTE/PATH/uv \
  --directory /ABSOLUTE/PATH/browser-automation \
  run --env-file /ABSOLUTE/PATH/browser-automation/.env browser-agent-mcp
codex mcp get browser-automation --json
```

Replace all paths with actual paths. Retain equivalent existing entries; do not overwrite a conflicting name or unrelated configuration. This explicitly loads the private `.env`; alternatively Codex's `env_vars` forwards selected names from its environment. Neither listing configuration nor key presence proves a working MCP tool or paid provider call: complete the runbook's protocol/`doctor` and skill-discovery checks, then reload/restart if needed.

### Claude Code

```bash
claude mcp add --transport stdio browser -- uv --directory /ABSOLUTE/PATH/browser-automation run browser-agent-mcp
claude mcp list
```

Review project/server trust prompts. Configure selected environment variables securely in the client; avoid putting keys into committed MCP config or shell history.

### Hermes

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  browser:
    command: uv
    args: ["--directory", "/ABSOLUTE/PATH/browser-automation", "run", "browser-agent-mcp"]
```

Then start `hermes chat`. Hermes does not blindly inherit the entire shell environment: add only needed key/consent variables to its explicit server `env` configuration. See [integration details and OMP extension setup](docs/integrations.md) for exact tools, approval records, JSONL examples, and the installed-public-API OMP integration.

## Safety, evidence, and development

CDP can access sensitive data throughout the profile. Models receive selected page text and screenshots when inference is enabled. Do not send passwords, private account data, or internal pages to a provider without authorization. Page content is untrusted; it cannot approve actions or override your goal/policy. Upload/download paths and approvals must come from the trusted host. Private artifacts, keys, profiles, and raw traces are ignored by Git.

No matched benchmark against Jev or Codex has been run. Browser-only latency, deterministic local-provider loop latency, and live paid-model end-to-end latency are distinct metrics. Offline provider tests do not prove a paid live API call; an isolated fixture does not prove attachment to a logged-in user profile. See [dated research and evidence boundaries](docs/research.md).

The authorized **eight-issue release gate, 2026-10-09**, passed **152 tests in 168.66s** (Python 3.13.12), with no reported skips/failures. It includes real Chromium, official stdio/current+legacy HTTP consumers, cancellation/progress, HTTP/WebSocket events, recovery policy/draft preservation, target-local visual guards, CLI and actual OMP loader/callbacks:

```bash
BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu \
  BROWSER_INTEGRATION_TESTS=1 uv run --extra http pytest --basetemp=/tmp/browser-release-final-proof
uv build
```

The host library path is explicit verification configuration, not a portable default. `uv build` packaged the new monitor/state modules and snapshot; a clean installed wheel outside the checkout actually launched Chromium, produced changed rendered PNGs, captured HTTP503/socket events over modern+legacy HTTP (21 tools), and delivered three stdio progress updates before independently verified success using a **local deterministic HTTP provider**, not a paid model. Earlier skill validation/actual Codex discovery remain recorded historical checks.

[All eight acceptance mappings, exact commands, intermediate failures/repairs, artifacts, checksums and limits](docs/research.md#eight-issue-release-gate--2026-10-09) are in research. A real logged-out Linux YouTube page returned 12 semantic targets matching its screenshot, with **partial** frame coverage and no video feed. This does not prove logged-in YouTube/Facebook/TikTok, personal native Chrome/macOS, headed desktop, live OCR/model quality, public TLS or benchmark superiority. Original tester reports and `doc.md` remain unchanged.

See [AGENTS.md](AGENTS.md), [worker instructions](agents/worker-instructions.md), [durable workstreams](agents/workstreams.json), and [changelog](CHANGELOG.md). Preserve the user's `doc.md`. Workers implement scoped features; the parent manages/reviews; the active GitOwner in workstreams serializes exact-path feature commits and pushes.
