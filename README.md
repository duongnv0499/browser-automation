# Browser Automation

Guarded browser control and a multimodal Luna Decisions agent for Python, Codex, Claude Code, Hermes, and OMP. Use your opted-in, logged-in Chrome **or explicitly choose** an isolated browser.

## Strengths

- Revision-bound element observations with compatible operations, stale/covered-target checks, and real Playwright input—not model-generated selectors or JavaScript.
- DOM text plus optional PNG screenshots, including visual-target observations for interfaces that cannot be described completely by HTML controls.
- OpenRouter alpha Decisions as the primary provider, with a distinct OpenAI Decisions adapter. One typed choice selects a compatible operation/target pair; field text uses a separate structured-output call.
- Persistent JSONL CLI and MCP stdio sessions; independent verification before a model-selected DONE counts as success.
- Explicit native-profile consent, owned-tab lifecycle, and approval-bound consequential actions/file transfers.

These are design capabilities, not claims of better performance than Jev or Codex. Supported surfaces, limitations, and verification evidence are documented below.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/duongnv0499/browser-automation.git
cd browser-automation
uv sync --locked
cp .env.example .env
# Edit .env: set OPENROUTER_API_KEY or OPENAI_API_KEY only if using a model.
uv run browser-agent doctor
```

For **isolated Chromium** also run:

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

## Explicit isolated browser

```bash
# Persistent visible browser, controlled through subsequent JSON lines:
uv run browser-agent launch
# Explicit headless browser is useful for server-side fixtures:
uv run browser-agent launch --headless
# Paid, bounded agent task in an explicitly chosen isolated browser:
uv run --env-file .env browser-agent run \
  'Open the Wikipedia article about browser automation; stop when its heading is visible.' \
  --url https://en.wikipedia.org --provider openrouter --max-steps 20
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

## Agent clients (MCP stdio)

Use an absolute checkout path so client working directories do not matter. Browser-only tools need no API key. To expose native sessions to a client, set `BROWSER_NATIVE_CONSENT=1` in the trusted host/server environment **after reviewing the access**, then call `connect_default`. Approval records and file directories are host policy, not model-controlled parameters.

### Codex

```bash
codex mcp add browser -- uv --directory /ABSOLUTE/PATH/browser-automation run browser-agent-mcp
codex mcp list
```

In `~/.codex/config.toml`, optionally forward selected variables rather than hardcoding secrets:

```toml
[mcp_servers.browser]
command = "uv"
args = ["--directory", "/ABSOLUTE/PATH/browser-automation", "run", "browser-agent-mcp"]
env_vars = ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "BROWSER_NATIVE_CONSENT", "BROWSER_NATIVE_PROFILE_DIRECTORY"]
tool_timeout_sec = 180
```

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

After all implementation work is ready and the parent authorizes coordinated verification:

```bash
uv run pytest
uv build
```

Real-browser checks require installed browsers and appropriate OS dependencies. Live provider checks need real keys/model access; native current-profile checks need opted-in running Chrome and explicit user approval. Save screenshots plus rendered DOM outcomes, not just a model's completion claim.

See [AGENTS.md](AGENTS.md), [worker instructions](agents/worker-instructions.md), [durable workstreams](agents/workstreams.json), and [changelog](CHANGELOG.md). Preserve the user's `doc.md`. Workers implement scoped features; the parent manages/reviews; Harness serializes feature commits and pushes.
