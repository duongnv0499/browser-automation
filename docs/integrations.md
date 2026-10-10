# Agent integrations

Stdio/JSONL retain browser state in one persistent worker; authenticated [Streamable HTTP](http.md) additionally lets compatible agents connect to one server on the browser host. The existing OMP extension remains stdio/JSONL. CDP endpoints must be loopback HTTP/WebSocket URLs; remote endpoints and URL credentials are rejected. Browser page contents and API keys are not written to routine logs. Observations are sensitive: the caller receives page content and optional screenshots, and autonomous runs send these to the selected model provider.

For “install this repo” in Codex, follow [the agent-executable install runbook](../install.md): default local stdio plus skill, absolute paths, preserved configuration/secrets, real MCP diagnostics, and client discovery. HTTP is opt-in; it does not automatically change the browser execution mode.

Browser tools navigate/read only HTTP(S) pages and `about:blank`. `file:`, `data:`, `javascript:`, and browser-internal pages are prohibited, including preexisting attached tabs: native attachment does not grant arbitrary local-file read access. Local file content enters the browser only through explicitly host-approved, directory-scoped uploads/downloads.

## Native first, isolated explicitly

With current Chrome, enable remote debugging in `chrome://inspect/#remote-debugging`, then explicitly allow the browser connection when Chrome prompts. This package does not enable debugging, relaunch your profile, or bypass Chrome consent. Native discovery reads the running profile's `DevToolsActivePort`; if needed supply the **user-data directory** (not the `Default` subdirectory). Default paths are platform-dependent; see browser documentation.

```sh
uv run browser-agent doctor
uv run browser-agent connect --consent --profile-dir "$HOME/.config/google-chrome"
# Older/developer CDP installations: only explicit loopback endpoints.
uv run browser-agent connect --endpoint http://127.0.0.1:9222
# Explicit isolated profile, visible by default:
uv run browser-agent launch
uv run browser-agent launch --headless
# Native goal run is the default; failure never falls back to isolated:
uv run browser-agent run --consent --url https://example.com 'Read the example domain page'
# Opt in to isolated autonomous run:
uv run browser-agent run --isolated --headless --url https://example.com 'Read the example domain page'
```

Autonomous screenshot mode defaults to the provider's declared vision capability, including `BROWSER_AGENT_VISION=false`. The CLI can explicitly override with `--no-screenshot` or `--screenshot`; supplying images to a text-only provider is an explicit configuration error. Example: `uv run browser-agent run --isolated --headless --no-screenshot --url https://example.com 'Read the example domain page'`.

`launch`/`connect` print a session description, then accept JSON-lines commands until stdin EOF. Do not pipe only one command and expect session IDs to survive process exit. `run` is a bounded self-contained goal command and closes its connection afterward. Closing attached sessions disconnects; preexisting tabs and Chrome remain open. `close_tab` accepts only tabs owned by this worker. A tab created in an attached browser may remain open after disconnect; close it explicitly if wanted.

Browser executable overrides are host policy: set `BROWSER_EXECUTABLE_PATH` before starting a worker or use the host CLI `--executable-path`. MCP cannot choose arbitrary executable paths. This does not sandbox a user who already grants an agent unrestricted shell access.

## Persistent JSON-lines CLI

Start `uv run browser-agent serve`, keep its stdin/stdout open, and send one JSON object per line. Each response echoes `id` and has either `result` or `error` (`code`, `message`). Distinct sessions can run concurrently; each session serializes commands under a lock. JSON-lines cancellation uses `{"command":"cancel","arguments":{"request_id":123}}` for an active request ID; it retains the session and unrelated requests. MCP uses standard cancellation notifications. On EOF outstanding operations are cancelled and owned resources cleaned up.

```json
{"id":1,"command":"launch","arguments":{"headless":false}}
{"id":2,"command":"new_tab","arguments":{"session_id":"RETURNED_SESSION","url":"https://example.com"}}
{"id":3,"command":"observe","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","screenshot":true}}
{"id":4,"command":"act","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","action":{"observation_id":"RETURNED_REVISION","operation":"click","target":"RETURNED_ELEMENT"}}}
{"id":5,"command":"text","arguments":{"session_id":"RETURNED_SESSION","observation_id":"RETURNED_REVISION","offset":12000,"limit":12000}}
{"id":6,"command":"navigate","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","url":"https://example.org/next"}}
{"id":7,"command":"close","arguments":{"session_id":"RETURNED_SESSION"}}
```

Use fresh observations after actions. Stale/covered/wrong-tab/unsupported actions fail rather than targeting a guessed selector. Direct external `act` uses the same observed-element risk policy as autonomous runs: consequential/custom/visual controls and potentially submitting keys pause for exact host approval rather than bypassing policy. Service keeps at most eight bounded observation metadata records globally, without screenshots; each browser session also retains eight revisions, so browser operations performed during `run` can evict earlier external snapshots. Full text continuation delegates to that browser revision cache. `text_length` reports the captured prefix length; `source_truncated` means source beyond the hard safety cap was omitted, and `next_offset: null` means the cache is exhausted, not that every DOM byte was captured. Evicted revisions require a fresh observation.

### Bounded navigation and semantic observation

`new_tab` accepts `wait_until` (`commit`, `domcontentloaded`, `load`) and `timeout_ms` (1–120000, default 15000). A bounded timeout returns the owned tab ID and `navigation_status: "timeout"`, not a closed uninspectable tab. Retain it, observe the rendered page, and explicitly close owned work when appropriate; never infer a site's blocking cause from a timeout alone. `networkidle` is not accepted.

`navigate` (`session_id`, `tab_id`, `url`, optional `wait_until`/`timeout_ms` with the same bounds) moves a tab this session owns (`new_tab` or its popups) and returns `{tab: {id, url, title}, navigation_status, wait_until}`; timeout retains the tab with a `navigation_timeout` diagnostic. Only HTTP(S) and `about:blank` are accepted (`prohibited_url` otherwise, before any browser call). A preexisting user tab is refused with `not_owned_tab` and `recommended_next_action: "new_tab"`; the user's page is left untouched. Navigating drops that tab's cached observations and paused approvals, so acting on an older revision returns `unknown_observation`; observe again first.

`act` accepts optional `settle_ms` (0–10000; default 1000 for `click`/`press`/`drag`, otherwise 0) and `timeout_ms`. Every act result includes `navigation: {started, status, url}`: `status` is `none` (no main-frame navigation started within the settle window, or `reason: "not_committed"`/`"page_closed"`), `complete` (fragment/`pushState` commit, or new document reached `domcontentloaded`) or `timeout`. A non-navigating click returns after at most the settle window, never the navigation timeout. This is a settle signal, not success: verify the rendered outcome with a fresh observation. See [settling after input](browser.md#settling-after-input).

`observe` adds `coverage` and DOM-derived `page_state`; `truncated: false` does not mean every rendered semantic control was collected. `interpret_visual: true` explicitly requests a screenshot plus paid multimodal interpretation (`provider`/`model` optional); it is not the same as merely capturing `screenshot: true`. DOM/vision provenance remains separate and provider failure is explicit. [Recovery policy](providers.md#operator-controlled-recovery) is host-only: snapshot-bound `reload` defaults to exact approval; a button's Refresh label grants no authority.

### Scoped traffic diagnostics

Single-tab `network_start`, `network_list`, `network_stop` and application `websocket_*` monitors use the retained `session_id`/`tab_id`. Network multi-tab tools are `network_start_many` (`tab_ids`, optional `include_new_tabs`), `network_list_many` (`tab_ids`, per-tab `cursors`) and `network_stop_many`. Start before interaction, inspect each capture's result and cursor/drop/history metadata, and stop the scope explicitly. List limits and max events bound retained evidence, not browser-wide completeness.

```json
{"id":7,"command":"network_start","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","max_events":256}}
{"id":8,"command":"network_list","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","limit":100}}
{"id":9,"command":"network_stop","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB"}}
```

HTTP 503 is a response status, not a transport failure. Lightweight list defaults remain sanitized metadata. `network_detail` accepts `request_id`, optional `fields` and `include_sensitive`; `network_body` accepts `request_id`, `part` (`request`/`response`), byte `offset`, `limit` and `include_sensitive`. Sensitive inspection additionally needs host `BROWSER_NETWORK_SENSITIVE=1`. Read `next_offset` and completeness/unavailable fields, including base64 binary chunks. These are actual selected request/response data, not reconstructed pre-capture history. Website WebSockets retain their separate host `BROWSER_MONITOR_PAYLOADS` opt-in and main-page CDP/control-frame limitations.

`network_call` takes `url`, optional `method`, `headers`, `params`, one of `body`/`json_body`/`form`/`body_base64`, `timeout_ms`, `max_redirects` and `prepare_only`. `network_replay` uses captured `request_id`, the same deliberate overrides and optional `target_tab_id`. Ordinary trusted same-origin safe reads return an actual response; other plans return `approval_required` and the exact binding. The host uses the existing interactive `browser-agent approve` workflow with that returned binding, then the caller invokes `network_execute(session_id, plan_id, approval_token)`. No raw credentials are in the approval preview. See [the network guide](network.md) for cookie context, privacy, all-method capability, redirect policy and independent UI verification.

`network_calls(session_id, tab_id, plan_id?, cursor?, limit?)` discovers bounded sanitized API-issued metadata separately from passive capture. Use the retained plan ID to find the issued request after cancellation prevented the normal final response; inspect its available detail/body and unknown-outcome diagnostic, rather than automatically retrying. Cursor is nonnegative and limit is 1–1,000. Same-URL navigation still changes document generation and invalidates an old plan.

### Incremental progress

JSONL opts in with a top-level `"progress": true` on the request. Interim `{"id":...,"progress":event}` records precede the normal final `result`/`error`; they never resolve a pending request. MCP opts in per call through `params._meta.progressToken`, not a tool argument. Notifications use the active token, monotonically increasing progress, compact semantic JSON messages and no invented total. The official SDK client uses `call_tool(..., progress_callback=async_callback)` with callback `(progress, total, message)`. [HTTP](http.md#progress-and-cancellation) uses the same public contract. Cancellation stops only that request; do not replay or close unrelated sessions. Progress is bounded page-state evidence, not raw screenshots/text or success proof.

## MCP stdio

The MCP server supports initialization, version negotiation, tools/list, tools/call, ping, cancellation, and EOF cleanup. Tools mirror the persistent CLI catalog, including browser lifecycle/control, monitoring, multi-tab network inspection and approved API execution. Discover the current catalog dynamically rather than checking a fixed count. `observe` returns compact structured/text metadata and screenshot in a separate `image/png` MCP image block, not a base64 string stuffed into text. Tool failures use `isError` with structured `error`; JSON-RPC failures use protocol errors.

Use absolute paths and do not put API keys directly into checked-in configuration. Primary sources: [Codex MCP](https://developers.openai.com/codex/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp).

### Codex

Follow [install.md](../install.md#3-register-mcp-idempotently) to inspect the intended name before adding it. Retain equivalent entries; preserve a conflicting entry and use an unused name or ask before replacement. With actual absolute paths and an unused name:

```sh
codex mcp add browser-automation -- /ABS/PATH/uv --directory /ABS/PATH/browser-automation \
  run --env-file /ABS/PATH/browser-automation/.env browser-agent-mcp
codex mcp get browser-automation --json
```

This explicitly loads the server's private `.env`, not a client-side secret in TOML. Alternatively forward only needed host variables in that server's `env_vars` (for example `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `BROWSER_NATIVE_CONSENT`, `BROWSER_NATIVE_PROFILE_DIRECTORY`, `BROWSER_FILES_DIRECTORY`, `BROWSER_APPROVALS_FILE`). Never enable consent or file access merely to finish installation. Check initialization, tool discovery, and `doctor`; configuration listing is not a successful tool call. Reload/restart Codex for newly registered tools when needed, preserving unrelated settings and approval policy.

### Claude Code

```sh
claude mcp add --transport stdio browser -- uv --directory /ABS/PATH/browser-automation run browser-agent-mcp
```

Set required keys and native consent in the host environment before starting Claude. Keep project-shared MCP configuration secret-free.

### Hermes

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  browser:
    command: uv
    args: [--directory, /ABS/PATH/browser-automation, run, browser-agent-mcp]
    env:
      BROWSER_NATIVE_CONSENT: "1"
      BROWSER_NATIVE_PROFILE_DIRECTORY: /ABS/PATH/ChromeUserData
```

Hermes explicitly controls the child environment; add the provider credential locally using its supported environment configuration, not into a repository. Native consent only authorizes attachment, not file access or purchases.

## Portable professional skill

The instruction-only `skills/browser-automation/SKILL.md` follows the Agent Skills standard. It recommends bounded Luna `run` goals rather than unnecessary step-by-step direct actions, preserves returned session IDs, distinguishes native/isolated browser choice, budgets page text/images, pauses for host-generated approvals, and requires independent completion evidence. It does not install tools or grant permissions. Tools work without it; clients may also consume server initialize instructions.

Codex self-installation is covered by [install.md](../install.md#4-install-the-professional-skill-safely). Retain a link already resolving to this exact source; a foreign or broken destination is a conflict, not successful installation. Confirm actual `/skills` discovery (or the supported non-model app-server API), and restart if automatic detection has not refreshed.

Install only into your intended client scope; do not overwrite an existing same-name skill. From the checkout, Codex and Claude's documented symlink support keeps the skill updated; keep the checkout at this absolute location. The guard rejects existing directories and broken symlinks alike (no `ln -f`). Hermes uses a guarded copy because symlink discovery is not assumed here.

```sh
skill_source="$PWD/skills/browser-automation"
# Codex: ~/.agents/skills; project alternative .agents/skills
mkdir -p "$HOME/.agents/skills"
skill_dest="$HOME/.agents/skills/browser-automation"
if [ ! -e "$skill_dest" ] && [ ! -L "$skill_dest" ]; then
  ln -s "$skill_source" "$skill_dest"
else
  printf '%s\n' "Existing skill retained: $skill_dest"
fi
# Claude Code: ~/.claude/skills; project alternative .claude/skills
mkdir -p "$HOME/.claude/skills"
skill_dest="$HOME/.claude/skills/browser-automation"
if [ ! -e "$skill_dest" ] && [ ! -L "$skill_dest" ]; then
  ln -s "$skill_source" "$skill_dest"
else
  printf '%s\n' "Existing skill retained: $skill_dest"
fi
# Hermes: default profile's primary ~/.hermes/skills directory
mkdir -p "$HOME/.hermes/skills"
skill_dest="$HOME/.hermes/skills/browser-automation"
if [ ! -e "$skill_dest" ] && [ ! -L "$skill_dest" ]; then
  cp -R "$skill_source" "$skill_dest"
else
  printf '%s\n' "Existing skill retained: $skill_dest"
fi
```

Codex supports explicit `$browser-automation` and `/skills` discovery, plus implicit description matching. Claude Code supports `/browser-automation`; restart/reload if the skill does not appear and review workspace trust. Hermes documents `/browser-automation` and `skills_list`/`skill_view` progressive disclosure; named profiles may have different home directories. These are documented discovery contracts, not a claim that all three installed clients were exercised. MCP tool names can have client-specific prefixes; choose the discovered logical `run`/session tools rather than hardcoding a prefix.

Example bounded prompt: “Use browser-automation. In my explicitly consented native session, read at most five relevant posts and return a shortlist plus reply drafts; do not post anything.” Facebook is only a read/draft workflow illustration, not an actual benchmark or permission for spam. Browser-only control needs no provider key; Luna `run` needs the server's Decisions credentials; HTTP bearer authentication uses a distinct MCP token. See [HTTP client connections](http.md) for Codex/Claude and generic SDK usage. OMP's existing extension does not gain HTTP support from installing this skill.

Primary sources accessed 2026-10-09: [Agent Skills specification](https://agentskills.io/specification), [Codex skills](https://developers.openai.com/codex/skills), [Claude Code skills](https://code.claude.com/docs/en/skills), [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills). Skill validation and actual client discovery evidence, when exercised, are recorded separately in [research](research.md).

## Host approval and local files

The model cannot mint approval or choose an allowed filesystem directory. Before starting the worker, the host sets `BROWSER_FILES_DIRECTORY=/ABS/allowed` and `BROWSER_APPROVALS_FILE=/ABS/private/approvals.json`. An unapproved `upload`/`download` returns `status: approval_required`, the exact `binding`, and a host command. Save that binding to a private `binding.json`, inspect the target/revision and paths, then **in your own terminal**:

```sh
uv run browser-agent approve --binding-file binding.json --approval-file /ABS/private/approvals.json
```

The host must type `APPROVE`; noninteractive stdin is rejected. This creates a five-minute token tied to that exact JSON binding. Repeat the file tool with the returned `approval_token`; it is consumed once in this worker. Browser stale checks still apply, and filesystem paths must stay inside the host directory. MCP/OMP expose no `approve` tool. Approval files are private host policy; never place them or tokens in source control. Do not hand an untrusted agent unrestricted shell access to these files.

Autonomous risky actions pause by default with `host_approval.binding`. Save that exact object and use the host approval command above. While the persistent worker remains alive, call `approved_act` with `session_id`, `tab_id`, `observation_id`, and the returned `approval_token`. It executes only the cached pending action, expires after five minutes, and still rejects stale browser revisions. Then call `run` again with the goal to continue and verify. Navigation/new observations may invalidate it; never loosen a binding just to resume. One-shot `run` closes its worker, so use MCP or persistent JSON-lines for interactive approvals. DONE requires separate model verification, not merely stopping.

## OMP extension

Installed OMP v18.8.0 exposes public extension loading (`-e`) and `pi.registerTool`/`pi.zod`. The actual extension is `integrations/omp/browser-tools.mjs`; it registers `browser_agent` and keeps one Python JSON-lines worker per extension factory. Its `command` enum mirrors the JSON-lines catalog, including `navigate` for session-owned tabs.

```sh
BROWSER_AGENT_PYTHON=/ABS/PATH/browser-automation/.venv/bin/python \
  omp -e /ABS/PATH/browser-automation/integrations/omp/browser-tools.mjs
```
Inside OMP, `/browser-agent-doctor` executes the same registered browser tool against the persistent Python worker and displays dependency/key-presence diagnostics without invoking a model. This is useful for checking interpreter configuration before opening a browser.


The package must be installed in that interpreter (`uv sync` from this repository does so). Configure native consent/provider keys on the host. Extension results separate screenshot image content from JSON text. Session shutdown ends worker stdin for cleanup. This integrates this project's own tools; it does **not** reuse or emulate OMP's private browser runtime, and does not claim performance superiority over OMP/Codex/Jev. If extension loading is unavailable on another OMP release, use its shell tool to start the documented persistent worker and keep the process stdin/stdout alive; repeated one-shot shell commands do not preserve browser identities.

The registered tool's fourth `execute` argument is the public OMP `onUpdate` callback. With it, the bridge requests JSONL progress and forwards bounded intermediate semantic events while retaining the pending request until its final result/error; interim frames never masquerade as completion. Cancellation uses the original request ID and does not terminate unrelated sessions. This is the OMP stdio bridge, not inferred HTTP support. Primary reference accessed 2026-10-09: [OMP extensions contract](https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/extensions.md).

JSONL worker guard errors are returned as a structured OMP tool result with `status: "error"` and the full safe `error` object, in both text content and `details.error`; they are not reduced to a thrown message-only exception. Changed-region and `recommended_next_action` diagnostics remain available to the parent. Recommendations never authorize replay or relax the snapshot guard.

## Reproducing integration verification

```sh
BROWSER_INTEGRATION_TESTS=1 uv run --with mcp pytest \
  tests/test_service.py tests/test_service_stdio.py tests/test_service_omp.py \
  --basetemp=/tmp/browser-integration-proof
```

These opt-in tests were exercised with real Chromium, the official MCP Python SDK 2.3.0, and installed OMP 18.8.0. They assert rendered DOM outcomes and save before/after PNGs, not merely echoed tool parameters. Scenarios include snapshot-bound clicks, host-approved drag, empty multi-select clearing, refused consequential click/Enter, non-clicking Escape focus, scoped upload/download contents, file-URL denial, real request cancellation, and preservation of a preexisting tab/cookie after CDP disconnect. OMP's registered tool is executed through `/browser-agent-doctor`; the RPC completion explicitly reports no model turn.

Text-only CLI goal tests use a deterministic **local HTTP Decisions fixture** with independent completion verification. They exercise the real provider wire/client and browser loop but are not live-model quality, cost, or latency evidence. The CDP test uses a disposable real browser with fixture session state: it does not establish that a third-party logged-in account or Chrome's real user-consent dialog was exercised. No live provider verification is claimed without supplied API credentials. If Chromium needs non-system shared libraries, configure the host's `BROWSER_AGENT_LIBRARY_PATH` explicitly.

