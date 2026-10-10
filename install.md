# Install Browser Automation for Codex

This is the agent-executable installation entrypoint. When the user says “install this repo,” **perform the reachable steps below**, rather than only returning commands. Default to local MCP **stdio** plus the instruction-only browser skill. Codex starts/stops the stdio subprocess; no daemon is needed. Choose HTTP only when requested. Installing tools does not authorize browser attachment, consequential actions, or paid model calls.

Repository: <https://github.com/duongnv0499/browser-automation>. Follow its `AGENTS.md`; preserve `doc.md`. Installation into the user's machine is not permission to modify this repository's source or commit secrets.

## 1. Inspect and select safe defaults

- Discover OS, the user's home, `git`, `uv`, and `codex` with the available tools. Inspect `codex mcp --help`, `codex mcp add --help`, and `codex mcp get --help` for the installed version. The commands below are POSIX examples; use equivalent native tools on Windows, not a guessed POSIX home.
- Prefer an already supplied checkout of this repository. Otherwise use `$HOME/.local/share/browser-automation`, or the user's explicit directory. Resolve it to an absolute path, called `INSTALL_DIR` below. Do not place it in a temporary directory: the MCP command and skill link depend on it staying there.
- Inspect an existing destination's Git origin and local changes before using it. A foreign/non-Git directory is a conflict: retain it and choose an unused directory or ask about the conflict. Never reset, delete, overwrite, or silently repoint an existing checkout. Reuse a matching checkout; if an update is requested, use a reviewed fast-forward only when local changes are preserved, otherwise explain the conflict.
- Preserve unrelated Codex configuration, MCP servers, skills, and credentials. Determine the actual Codex configuration scope, including a user-supplied `CODEX_HOME` and trusted project overrides. Do not overwrite a whole `config.toml`.
- No model API key is required to install, discover tools, or run `doctor`. Do not ask for a secret in chat. Paid Luna tasks additionally require provider access/billing, and browser work requires an explicit execution choice.

If dependencies are missing, install only through a user-authorized package/install mechanism. For uv, use the [current official installation instructions](https://docs.astral.sh/uv/getting-started/installation/): for example `pipx install uv` when pipx is present or `brew install uv` when Homebrew is available. The official standalone script is `https://astral.sh/uv/install.sh`; download and inspect it before an authorized execution, rather than blindly piping a download to a shell. On Windows use the documented package-manager route or review the PowerShell installer. Do not elevate permissions, weaken execution policy, or modify shell profiles without approval. If Codex CLI is unavailable, use the installed client's supported MCP settings or report that exact prerequisite; do not invent CLI results.

Python 3.11+ is required. uv can provision managed Python; where downloading runtimes needs separate approval, obtain it first. If network/sandbox policy prevents installation, finish all independent steps and report the precise denied prerequisite.

## 2. Install the local tool

The following assumes the inspected destination **does not exist**; skip cloning for an existing matching checkout:

```sh
INSTALL_DIR="$HOME/.local/share/browser-automation"
mkdir -p "$(dirname "$INSTALL_DIR")"
git clone https://github.com/duongnv0499/browser-automation.git "$INSTALL_DIR"
```

Resolve the actual uv executable to an absolute path (`command -v uv`, or the platform equivalent). Use that path, not a bare `uv`, in Codex configuration. Set `UV_BIN` and `INSTALL_DIR` to the discovered absolute paths; retain these variables for subsequent examples.

```sh
UV_BIN="$(command -v uv)"
# Confirm UV_BIN and INSTALL_DIR are absolute before proceeding.
"$UV_BIN" --directory "$INSTALL_DIR" sync --locked
```

Use `sync --locked --extra http` instead if setting up an HTTP **server** on this machine. Registering an already running remote HTTP server does not require installing server dependencies or a local browser.

Create `.env` from `.env.example` **only when absent**, with private permissions. Do not display or replace existing contents, including on repeated installations. For example, after sync:

```sh
"$UV_BIN" --directory "$INSTALL_DIR" run python - "$INSTALL_DIR" <<'PY'
import os
import sys
from pathlib import Path
root = Path(sys.argv[1])
target = root / ".env"
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing .env retained; values not displayed")
else:
    with os.fdopen(fd, "wb") as output:
        output.write((root / ".env.example").read_bytes())
    print("Created private .env; model keys are optional")
PY
"$UV_BIN" --directory "$INSTALL_DIR" run --env-file "$INSTALL_DIR/.env" browser-agent doctor
```

On Windows, also apply a user-only ACL appropriate to the host; POSIX mode bits alone are not a Windows privacy guarantee. Report API-key **presence only**. Preserve existing credentials and host settings; warn privately about unsafe permissions rather than printing secrets. `.env` is explicitly loaded by `uv --env-file`; the package does not auto-load it. Never put keys in MCP TOML, command arguments, shell history, Git, or diagnostic artifacts.

`BROWSER_APPROVAL_MODE` (`strict` | `standard` | `autonomous`) is host policy for the **MCP server process**, never a tool argument. When unset it is `standard`: consequential actions (submits, POST forms, delete/send/log-out-style controls, uploads/downloads, state-changing or foreign-origin API plans, risky navigation) and all critical actions pause for an exact host token. Ordinary clicks and Enter do not pause.
- **Install default:** leave it unset unless the operator explicitly chooses a mode. Do not select `autonomous` merely to finish installation or to avoid prompts.
- **Where to set it:** in the server's private `.env` (loaded by `--env-file`), or in the registration's server environment: Codex `codex mcp add NAME --env BROWSER_APPROVAL_MODE=standard -- ...` or `[mcp_servers.NAME.env]`, Claude Code `claude mcp add --env BROWSER_APPROVAL_MODE=standard --transport stdio NAME -- ...`, or the Hermes `mcp_servers.NAME.env` mapping. For HTTP, set it in the browser-host server's environment, not the client's.
- **`autonomous`:** grants standing approval for everything except critical payment, credential/sensitive-input, account-deletion and credential-header actions. Reserve it for trusted tasks in an isolated or dedicated profile.
- **Checking:** `doctor` reports `approval_mode` (or `approval_mode_error`), and an invalid value fails every other tool.

See [host approval modes](docs/integrations.md#host-approval-modes).

Leave `BROWSER_RECOVERY_POLICY`, `BROWSER_MONITOR_PAYLOADS` and `BROWSER_NETWORK_SENSITIVE` unset unless the operator explicitly requests their reviewed scope; installation must not grant reload exceptions or sensitive traffic disclosure. Ordinary sanitized network detail/body inspection needs no provider key. Calls/replay support the browser cookie context across multiple tabs: ordinary same-origin safe reads execute directly, while consequential plans need exact host approval; the operator may require approval for all calls with `BROWSER_NETWORK_REQUIRE_APPROVAL=1`. Installation does not approve account API operations. `interpret_visual` remains a separate opt-in paid call and progress a per-call opt-in, not OS-screen monitoring. See [network workflow/privacy](docs/network.md) and [recovery boundaries](docs/providers.md#operator-controlled-recovery).

## 3. Register MCP idempotently

### Default: local stdio

Prefer MCP name `browser-automation`. Inspect first:

Treat `mcp get --json` output as private: an existing configuration may contain inline environment/header secrets. Inspect locally through a non-echoing capture, redact values before recording artifacts or reports, and do not stream raw configuration into chat or routine tool logs. Compare secret-bearing settings privately; only report their names/presence, never values.

```sh
MCP_NAME=browser-automation
codex mcp get "$MCP_NAME" --json
```

Distinguish “not configured” from a CLI/configuration error. If an enabled entry already uses the same absolute uv command, checkout, env file, and stdio arguments below, retain it. Inspect any extra restrictions/environment settings without exposing values; do not remove user policy. If the name belongs to a different configuration, retain it and choose an unused project-specific name (for example `browser-automation-local`), or obtain explicit permission before replacing it. Do not repeatedly add equivalent servers under new names. Register only the absent chosen name:

```sh
codex mcp add "$MCP_NAME" -- "$UV_BIN" --directory "$INSTALL_DIR" \
  run --env-file "$INSTALL_DIR/.env" browser-agent-mcp
codex mcp get "$MCP_NAME" --json
```

The saved command must contain actual absolute paths, not literal `$UV_BIN`, `$INSTALL_DIR`, `~`, or placeholders. Inspect the resulting transport/arguments and preserve unrelated entries. CLI output/config listing alone does **not** prove the server works. Existing disabled/filtered entries require a user-approved enablement or a separate unused name; do not silently loosen policy.

The `.env` supplies server credentials, consent and approval-mode policy. Alternatively, advanced hosts can forward only required environment variable names using Codex `env_vars`; do not mix conflicting credential sources or hardcode secret values. See [integrations](docs/integrations.md#mcp-stdio).

### Opt-in: Streamable HTTP

First identify whether the user wants to **connect to an existing server** or **set up the server on the browser host**. Do not create a local server when the request is remote registration only.

For an existing server, obtain its operator-approved `/mcp` URL and securely provision the matching bearer token in the **Codex client's environment**, named `BROWSER_MCP_TOKEN` (or the approved variable name). Never print the token or embed it in config. A server-side token alone does not populate the remote client's environment. `127.0.0.1` means the client's own machine; remote use needs a protected tunnel or reviewed HTTPS endpoint. This server uses bearer authentication, not an OAuth login flow.

Use the same inspect/retain/conflict logic as stdio, comparing URL and bearer variable. Then, only for an absent chosen name:

```sh
# MCP_URL is the actual approved URL, not a placeholder.
codex mcp add "$MCP_NAME" --url "$MCP_URL" \
  --bearer-token-env-var BROWSER_MCP_TOKEN
codex mcp get "$MCP_NAME" --json
```

For new server setup, perform sections 1–2 on the **browser host** with `sync --locked --extra http`, then follow [HTTP token generation and server startup](docs/http.md#install-and-start). Preserve a valid existing token; generate a new private credential only if absent, never rotate one merely by reinstalling. Supply provider keys only on that server host. Start with loopback and keep the foreground process alive, or use a separately approved user-managed process supervisor. Do not claim persistence across logout/reboot from a foreground command. Do not automatically install a system service, expose CDP, change firewalls, edit global shell profiles, or restart/configure the user's browser. Client environment provisioning and server environment provisioning are separate tasks; avoid logging either secret.

## 4. Install the professional skill safely

Default Codex user scope is `$HOME/.agents/skills/browser-automation`; project scope `.agents/skills/browser-automation` is an explicit alternative. Source is the absolute `$INSTALL_DIR/skills/browser-automation` directory, with `SKILL.md` intact.

1. If the destination is absent (including no broken symlink), create the parent directory and a symlink to the source.
2. If it already resolves to that exact source, retain it: installation is already complete.
3. If it is a foreign directory/link or broken link, **do not overwrite it**. Report the conflict and choose a user-approved alternate scope or resolve it with the user. Do not claim the repository's skill was installed merely because some same-named skill exists.

For the absent case only:

```sh
mkdir -p "$HOME/.agents/skills"
ln -s "$INSTALL_DIR/skills/browser-automation" "$HOME/.agents/skills/browser-automation"
```

Codex officially supports symlink discovery. On systems where symlinks are unavailable, use a user-approved directory copy into an absent destination, and explain that later updates require refreshing the copy; never force replacement. The skill is guidance, not an access-control mechanism or prerequisite for MCP tools. Do not change its browser workflow into an installer.

## 5. Verify the actual protocol and discovery

Do not stop at `codex mcp list`. Prefer the reloaded client's actual MCP tool catalog and a successful `doctor` tool call. Discover the catalog dynamically, including network multi-tab/detail/body/call/replay/execute tools; do not require an obsolete fixed count. If the installing conversation cannot refresh tools, use the official SDK against the **same configured transport** as below, then explicitly distinguish protocol health from current-session Codex tool availability. Do not use a paid model turn for installation diagnostics.

For stdio, run this temporary diagnostic through the checkout's Python using the official SDK as an ephemeral dependency (no repository/lock changes). It opens a real subprocess, negotiates initialization, lists tools, calls `doctor`, checks failure status, and closes the subprocess. Use the actual registration arguments/environment; if registration differs, adapt to it rather than testing a different server.

```sh
"$UV_BIN" --directory "$INSTALL_DIR" run --with 'mcp==2.3.0' python - "$UV_BIN" "$INSTALL_DIR" <<'PY'
import asyncio
import sys
from mcp import Client, StdioServerParameters

async def main():
    uv, root = sys.argv[1:]
    server = StdioServerParameters(
        command=uv,
        args=["--directory", root, "run", "--env-file", root + "/.env", "browser-agent-mcp"],
    )
    async with Client(server, mode="legacy") as client:
        print("Negotiated protocol:", client.protocol_version)
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        required = {"doctor", "connect_default", "launch", "observe", "act", "run", "close"}
        if not required <= names:
            raise RuntimeError("Expected browser tools missing")
        result = await client.call_tool("doctor", {})
        if result.is_error:
            raise RuntimeError("MCP doctor failed")
        print("Tool discovery and doctor succeeded; no browser/model invoked")
        print(result.structured_content)

asyncio.run(main())
PY
```

For HTTP, use the [official SDK diagnostic](docs/http.md#generic-official-python-sdk-client) with the actual approved URL and **client-side** token environment; successful authenticated discovery/`doctor` is required. Do not leak headers in debug output. An absent token, unreachable server, failed initialization, or error result is a real blocker, not successful installation.

Confirm the repository's skill appears in Codex `/skills` or the supported non-model `skills/list` app-server API, including its resolved source path. A filesystem link alone is not client discovery proof. New skills are normally detected automatically; restart Codex if absent. MCP configuration changes may require restarting/reconnecting the client or reloading the IDE extension. Newly registered tools might not enter the **current installing conversation**; start a fresh session and check `/mcp` and `/skills`. Do not claim refreshed discovery unless observed. Respect trust/approval settings and do not edit them to make a diagnostic pass.

Re-run registration inspection and the skill/env checks to establish idempotence: equivalent MCP entry retained, correct skill link retained, existing `.env` unchanged, unrelated config untouched. Preserve all private fixtures/artifacts outside Git.

## 6. Separate installation from browser/model readiness

Default installation verification is diagnostic only: do not attach Chrome, navigate account pages, or invoke Luna. Finish installing even when no model key or browser consent is supplied; report these as separate readiness states.

- **Native**: explain that CDP can access the whole profile. The user must explicitly opt in inside their running Chrome and approve its connection prompt. Only after that consent may the trusted host set `BROWSER_NATIVE_CONSENT=1` (and optional user-data directory) for the server and call `connect_default`. Never enable debugging, relaunch Chrome, reuse a locked profile, or silently fall back to isolation. See [native browser setup](docs/browser.md).
- **Isolated**: only when explicitly selected, install Chromium with `"$UV_BIN" --directory "$INSTALL_DIR" run playwright install chromium`. OS dependency installation/elevation needs permission; honor an approved host executable/library configuration rather than inventing a portable path. A safe optional check is `launch` (headless only if selected), `new_tab` on an approved public diagnostic page, `observe`, then `close`; preserve returned IDs and do not interact with accounts or consequential controls.
- **Luna**: the user securely sets `OPENROUTER_API_KEY` or `OPENAI_API_KEY` on the server host. Selected page text/screenshots are sent to the provider and calls may incur charges. Presence is not verified provider access; no paid run unless requested. Native consent is not approval for posting, purchases, or file transfers; host-generated exact-action approvals remain mandatory.

## Completion report

Report the actual install directory, uv path, MCP name/transport (HTTP URL if appropriate), configuration scope, skill source/destination and observed discovery, protocol/tool/doctor results, API-key **presence only**, browser readiness, and any exact unavailable prerequisite. List unverified categories explicitly: current-session reload, native attachment, isolated browser launch, paid provider calls, remote/TLS deployment, or persistence, as applicable. Do not call a partially blocked tool installation complete; do distinguish “tools installed and diagnostic passed; browser/model not configured” from a failure.

## Sources and evidence boundary

Primary contracts accessed **2026-10-09**: [Codex MCP](https://developers.openai.com/codex/mcp) (redirects to [current MCP docs](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)), [Codex skills](https://developers.openai.com/codex/skills) (redirects to [Build skills](https://learn.chatgpt.com/docs/build-skills)), [uv installation](https://docs.astral.sh/uv/getting-started/installation/), [SDK transports](https://py.sdk.modelcontextprotocol.io/client/transports/index.md), and [SDK negotiation](https://py.sdk.modelcontextprotocol.io/protocol-versions/index.md). Codex supports local subprocess/HTTP bearer configuration, `env_vars`, and symlinked user/project skills. SDK `Client(..., mode="legacy")` explicitly negotiates initialization for this stdio server; HTTP can use modern discovery. Installed Codex CLI help must still be inspected for version-specific flags. Documented support is not proof an autonomous Codex conversation, personal browser, or paid provider call was exercised; actual repository evidence lives in [research](docs/research.md).

### Exercised installation recipe

On **2026-10-09**, the authorized installation proof cloned published commit `3f3cfc5d9a7eec1480c513decae53671c10c813c` into a temporary home with isolated `HOME`/`CODEX_HOME` and executed the documented setup using actual uv and Codex CLI. The exact stdio SDK snippet above negotiated `2025-06-18`, discovered all 15 tools, and successfully called `doctor` with no provider keys or native consent. Actual Codex app-server `skills/list` discovered the linked repository skill in user scope. Repeated setup retained identical `.env`/Codex configuration bytes and the correct skill link; unrelated MCP configuration and a private environment fixture were preserved. Conflict fixtures retained a foreign MCP entry while selecting `browser-automation-local`, rejected a broken foreign skill link without replacement, and rejected a foreign checkout origin without touching its user marker.

The HTTP branch also registered a bearer-token environment reference through actual Codex CLI, started a temporary loopback foreground server, and used the official SDK to discover 15 tools and call `doctor` over negotiated `2026-07-28`. Its private token file was mode `0600`; the token was not logged, and the server was stopped after the probe. Machine-local evidence: `/tmp/browser-agent-install-fmtf6879/report.json` (not a durable public artifact).

This verifies an agent-executable recipe with real installation/configuration/protocol/discovery tools, **not an autonomous paid Codex installation conversation**. Existing uv/Codex installations and shared uv/Python caches were used; clean-machine dependency provisioning was not tested. No browser launch, personal native attachment/consent dialog, paid provider call, public TLS deployment, or reboot-persistent service was exercised. No runtime code changed and no full browser suite was rerun for this documentation-only installation proof.
