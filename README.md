# Browser Automation

Guarded browser control and a multimodal Luna Decisions agent for Python, Codex, Claude Code, Hermes, and OMP. Use your opted-in, logged-in Chrome **or explicitly choose** an isolated browser.

## Strengths

- Revision-bound element observations with compatible operations, stale/covered-target checks, and real Playwright input—not model-generated selectors or JavaScript.
- DOM text plus optional PNG screenshots, including visual-target observations for interfaces that cannot be described completely by HTML controls.
- OpenRouter alpha Decisions as the primary provider, with a distinct OpenAI Decisions adapter. One typed choice selects a compatible operation/target pair; field text uses a separate structured-output call.
- Persistent JSONL CLI, default MCP stdio and optional authenticated Streamable HTTP; independent verification before a model-selected DONE counts as success.
- Explicit native-profile consent, owned-tab lifecycle, and approval-bound consequential actions/file transfers.
- Structured DOM coverage/page state, opt-in provider visual summaries and incremental bounded-run progress; recovery remains host-policy/approval controlled.
- Bounded owned-tab navigation, multi-tab network capture with query/header/payload/response-body inspection, browser-context API calls/replay, and application-WebSocket diagnostics.

These are design capabilities, not claims of better performance than Jev or Codex. Supported surfaces, limitations, and verification evidence are documented below.

For agent-native diagnosis, see [navigation/coverage and frame safety](docs/browser.md), [visual provenance/recovery authority](docs/providers.md#page-state-and-opt-in-visual-interpretation), and [monitor/progress tools](docs/integrations.md#bounded-navigation-and-semantic-observation). These capabilities do not establish a live YouTube/Facebook/TikTok repair, personal macOS/native-profile proof or live-model quality; dated exercised evidence and limits are in [research](docs/research.md).

For DevTools-style API work, follow [the network workflow](docs/network.md): list tabs, start capture before acting, inspect request/response chunks, then call or replay in the selected browser cookie context. Ordinary same-origin safe reads execute directly; consequential/foreign-origin/credential-edited plans require exact host approval. All authorized HTTP(S) endpoints and methods remain callable, and API responses are distinct from rendered UI verification.

## Agent-friendly autonomy

External agents (Codex, Claude Code, Hermes, OMP) can drive the browser step by step without a human confirming every ordinary click, while the guards stay authoritative:

- **`navigate`** moves a tab this session owns (`new_tab` or its popups) to an HTTP(S)/`about:blank` URL. A preexisting user tab is refused with `not_owned_tab` (use `new_tab`). A timeout keeps the tab and reports `navigation_status: "timeout"`.
- **Settle after input.** `act` accepts `settle_ms` (0–10000; default 1000 for click/press/drag) and reports `navigation: {started, status, url}`. A click that starts no navigation returns after the settle window, not the navigation timeout. Wheel `scroll` waits (≤1.5 s) for scrolling to stop and reports `scroll: {settled, moved}`. These are settle signals, not success: observe again to verify.
- **Compact `observe` by default.** Elements are returned as `{id, role, name, ops}` plus only non-default state. `detail: "full"` returns the complete previous shape (needed for x/y refinement), `visual_regions: true` lists screenshot-grid targets, and `text_scope: "document"` reads text beyond the viewport. On single real-page runs, compact output was about a third of the full size (Hacker News 84,122 → 26,805 B; Wikipedia 38,975 → 12,201 B).
- **Earlier, clearer guard results.** Elements whose input point is covered are listed with `covered: true` and no operations. `act` revalidates an observation before minting an approval, so a stale revision fails at once instead of after the host approves. Cached observations are dropped after input. `observe` absorbs brief post-navigation churn with a bounded read-only retry (`settle_retries`).
- **Host approval modes.** `BROWSER_APPROVAL_MODE` is host policy set in the server environment, never a tool argument. One classifier assigns each action a tier: none, ordinary, consequential or critical.

| Mode | Pauses for a per-action host approval token |
|---|---|
| `strict` | ordinary and above: the previous behaviour, with identical decisions and reasons |
| `standard` (default) | consequential and above (submit/POST forms, delete/send/publish/log-out-style controls, uploads/downloads, state-changing or foreign-origin API plans, risky navigation) |
| `autonomous` | critical only. The host grants standing approval for everything else |

Critical actions always pause in every mode: payment buttons, submit controls and forms, sensitive or credential input, account deletion, and credential-header edits. A plain link with a payment-like name is a GET navigation and is capped at consequential. An action that runs with a non-`none` tier returns `approval: {source, mode, tier, reason}`. `source` is `host_policy` for standing approval and `host_token` for an exact token. The audit records authority, not success. Set the mode in the MCP registration's server environment:

```bash
# Codex (or put BROWSER_APPROVAL_MODE=... in the private .env loaded by --env-file)
codex mcp add browser-automation --env BROWSER_APPROVAL_MODE=standard -- /ABSOLUTE/PATH/uv \
  --directory /ABSOLUTE/PATH/browser-automation run --env-file /ABSOLUTE/PATH/browser-automation/.env browser-agent-mcp
# Claude Code (another option must separate --env from the server name)
claude mcp add --env BROWSER_APPROVAL_MODE=standard --transport stdio browser -- \
  uv --directory /ABSOLUTE/PATH/browser-automation run browser-agent-mcp
```

```yaml
# Hermes ~/.hermes/config.yaml
mcp_servers:
  browser:
    command: uv
    args: ["--directory", "/ABSOLUTE/PATH/browser-automation", "run", "browser-agent-mcp"]
    env:
      BROWSER_APPROVAL_MODE: standard
```

Use `autonomous` only for trusted tasks in an isolated or dedicated browser profile, never as a default for a personal logged-in profile. It lets an agent delete, send, submit, log out, call state-changing APIs and transfer files inside `BROWSER_FILES_DIRECTORY` without asking. An invalid value fails every tool call except `doctor`, which reports the mode or the error; it is never treated as a more permissive mode. Model providers still receive the page text and screenshots you send them, and CDP still exposes the whole attached profile. See [host approval modes](docs/integrations.md#host-approval-modes) and [settling after input](docs/browser.md#settling-after-input).

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

The **autonomy round gate, 2026-10-11** ran on macOS (Darwin 27, Python 3.13 via uv, Playwright 1.63.0, Chromium headless shell). At `e8d79f9`, `BROWSER_INTEGRATION_TESTS=1 uv run --extra http pytest tests --deselect tests/test_service_omp.py::test_actual_omp_extension_load` passed **273 tests, 1 deselected, in 141.61s**, and `uv build` produced the sdist and wheel.
- The deselected OMP extension-load test cannot run here because the local `omp` 18.2.1 lacks `--no-ui`.
- The later scroll-settle fix (`fix(act)`) passed 95 scoped tests. Its 3 attached-external-Chrome tests were **not rerun** after that change because the Mac screen was locked; they passed in the gate above.
- Real-site sizes and smokes (Hacker News, Wikipedia) are browser-only, single runs on an uncontrolled network.
- The parent's own MCP client session checked the tiers:
  - `standard`: an ordinary Add ran with an audit; "Delete all" and a password fill paused.
  - `autonomous`: "Delete all" ran; the password fill still paused.

No personal native Chrome, paid model call or competitor comparison was exercised. See [the autonomy evidence](docs/research.md#autonomy-round-gate--2026-10-11). The earlier gates below remain historical evidence.

The authorized **network release gate, 2026-10-09**, passed **196 tests in 299.70s** (Python 3.13.12), with no reported skips/failures. It includes real Chromium, detailed/binary network data, multi-tab/pop-up capture, cookie-context calls/replay, exact approvals and cancellation recovery, official stdio/current+legacy HTTP consumers, CLI and actual OMP loader/callbacks, alongside earlier browser/agent safety coverage:

```bash
BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu \
  BROWSER_INTEGRATION_TESTS=1 uv run --extra http pytest --basetemp=/tmp/browser-network-release-final-proof
uv build
```

The host library path is explicit verification configuration, not a portable default. Build succeeded; a **clean installed wheel outside the checkout** then passed **8 real-client/browser cases in 33.15s** through official SDK stdio/current+legacy HTTP and persistent CLI: two tabs plus popup, actual request detail/chunks, approved edited POST replay into the other tab, matching browser cookies/updated jar, HTTP503, binary bytes and discoverable cancelled outcomes with exactly one server POST. The installed catalog had **30 tools**; clients must discover it dynamically. API outcomes remain separate from the unchanged `Quiet UI` screenshot, not fabricated DOM success.

[Current feature acceptance, exact commands, scoped repairs, artifacts, hashes and limitations](docs/research.md#network-release-gate--2026-10-09) are recorded separately from the [historical eight-issue 152-test gate](docs/research.md#eight-issue-release-gate--2026-10-09). The latter's partial logged-out YouTube observation does not prove logged-in YouTube/Facebook/TikTok repair. Neither gate establishes personal native Chrome/macOS, paid OCR/model quality, headed desktop, production TLS or competitor superiority. Original tester reports and `doc.md` remain unchanged.

See [AGENTS.md](AGENTS.md), [worker instructions](agents/worker-instructions.md), [durable workstreams](agents/workstreams.json), and [changelog](CHANGELOG.md). Preserve the user's `doc.md`. Workers implement scoped features; the parent manages/reviews; the active GitOwner in workstreams serializes exact-path feature commits and pushes.
