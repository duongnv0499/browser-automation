# Local agent integrations

All transports retain browser state in one worker. No TCP control server is exposed. CDP endpoints must be loopback HTTP/WebSocket URLs; remote endpoints and URL credentials are rejected. Browser page contents and API keys are not written to routine logs. Observations are sensitive: the caller receives page content and optional screenshots, and autonomous runs send these to the selected model provider.

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

`launch`/`connect` print a session description, then accept JSON-lines commands until stdin EOF. Do not pipe only one command and expect session IDs to survive process exit. `run` is a bounded self-contained goal command and closes its connection afterward. Closing attached sessions disconnects; preexisting tabs and Chrome remain open. `close_tab` accepts only tabs owned by this worker. A tab created in an attached browser may remain open after disconnect; close it explicitly if wanted.

## Persistent JSON-lines CLI

Start `uv run browser-agent serve`, keep its stdin/stdout open, and send one JSON object per line. Each response echoes `id` and has either `result` or `error` (`code`, `message`). Commands execute serially; session locks also serialize concurrent MCP calls for the same browser.

```json
{"id":1,"command":"launch","arguments":{"headless":false}}
{"id":2,"command":"new_tab","arguments":{"session_id":"RETURNED_SESSION","url":"https://example.com"}}
{"id":3,"command":"observe","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","screenshot":true}}
{"id":4,"command":"act","arguments":{"session_id":"RETURNED_SESSION","tab_id":"RETURNED_TAB","action":{"observation_id":"RETURNED_REVISION","operation":"click","target":"RETURNED_ELEMENT"}}}
{"id":5,"command":"text","arguments":{"session_id":"RETURNED_SESSION","observation_id":"RETURNED_REVISION","offset":12000,"limit":12000}}
{"id":6,"command":"close","arguments":{"session_id":"RETURNED_SESSION"}}
```

Use fresh observations after actions. Stale/covered/wrong-tab/unsupported actions fail rather than targeting a guessed selector. Text continuation is cached at the same observation revision; 32 snapshots are retained, up to one million characters each. `source_truncated` reports the browser's source cap; `next_offset: null` means the cached text is exhausted, not a promise that every possible DOM byte was captured.

## MCP stdio

The MCP server supports initialization, version negotiation, tools/list, tools/call, ping, cancellation, and EOF cleanup. Its tools mirror the CLI commands above plus `doctor`, `tabs`, `close_tab`, `run`, `upload`, `download`, `connect_default`. `observe` returns compact structured/text metadata and screenshot in a separate `image/png` MCP image block, not a base64 string stuffed into text. Tool failures use `isError` with structured `error`; JSON-RPC failures use protocol errors.

Use absolute paths and do not put API keys directly into checked-in configuration. Primary sources: [Codex MCP](https://developers.openai.com/codex/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp).

### Codex

```sh
codex mcp add browser -- uv --directory /ABS/PATH/browser-automation run browser-agent-mcp
```

In `~/.codex/config.toml`, forward only needed variables in the server's `env_vars`:

```toml
[mcp_servers.browser]
command = "uv"
args = ["--directory", "/ABS/PATH/browser-automation", "run", "browser-agent-mcp"]
env_vars = ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "BROWSER_NATIVE_CONSENT", "BROWSER_NATIVE_PROFILE_DIRECTORY", "BROWSER_FILES_DIRECTORY", "BROWSER_APPROVALS_FILE"]
```

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

## Host approval and local files

The model cannot mint approval or choose an allowed filesystem directory. Before starting the worker, the host sets `BROWSER_FILES_DIRECTORY=/ABS/allowed` and `BROWSER_APPROVALS_FILE=/ABS/private/approvals.json`. An unapproved `upload`/`download` returns `status: approval_required`, the exact `binding`, and a host command. Save that binding to a private `binding.json`, inspect the target/revision and paths, then **in your own terminal**:

```sh
uv run browser-agent approve --binding-file binding.json --approval-file /ABS/private/approvals.json
```

The host must type `APPROVE`; noninteractive stdin is rejected. This creates a five-minute token tied to that exact JSON binding. Repeat the file tool with the returned `approval_token`; it is consumed once in this worker. Browser stale checks still apply, and filesystem paths must stay inside the host directory. MCP/OMP expose no `approve` tool. Approval files are private host policy; never place them or tokens in source control. Do not hand an untrusted agent unrestricted shell access to these files.

Autonomous risky actions pause by default with `host_approval.binding`. Save that exact object and use the host approval command above. While the persistent worker remains alive, call `approved_act` with `session_id`, `tab_id`, `observation_id`, and the returned `approval_token`. It executes only the cached pending action, expires after five minutes, and still rejects stale browser revisions. Then call `run` again with the goal to continue and verify. Navigation/new observations may invalidate it; never loosen a binding just to resume. One-shot `run` closes its worker, so use MCP or persistent JSON-lines for interactive approvals. DONE requires separate model verification, not merely stopping.

## OMP extension

Installed OMP v18.8.0 exposes public extension loading (`-e`) and `pi.registerTool`/`pi.zod`. The actual extension is `integrations/omp/browser-tools.mjs`; it registers `browser_agent` and keeps one Python JSON-lines worker per extension factory.

```sh
BROWSER_AGENT_PYTHON=/ABS/PATH/browser-automation/.venv/bin/python \
  omp -e /ABS/PATH/browser-automation/integrations/omp/browser-tools.mjs
```

The package must be installed in that interpreter (`uv sync` from this repository does so). Configure native consent/provider keys on the host. Extension results separate screenshot image content from JSON text. Session shutdown ends worker stdin for cleanup. This integrates this project's own tools; it does **not** reuse or emulate OMP's private browser runtime, and does not claim performance superiority over OMP/Codex/Jev. If extension loading is unavailable on another OMP release, use its shell tool to start the documented persistent worker and keep the process stdin/stdout alive; repeated one-shot shell commands do not preserve browser identities.
