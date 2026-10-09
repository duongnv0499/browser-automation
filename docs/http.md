# Run once, connect compatible agents

Streamable HTTP is an **additional** transport; `browser-agent-mcp` without flags remains stdio. Start one persistent server on the machine that owns the browser, then connect supported clients to `/mcp`. The remote agent controls that server's browser, not its own local Chrome. Native Chrome opt-in and host approval remain separate from MCP authentication.

“Any agent” means a client supporting the negotiated MCP Streamable HTTP protocol and bearer headers. It does not mean every product/version, legacy SSE-only clients, or a guarantee of model/tool behavior. Codex and Claude Code document HTTP support; the existing OMP extension remains a persistent **stdio/JSONL** bridge. HTTP does not expose CDP, an OAuth service, or browser-provider API keys.

For Codex “install this repo,” use [install.md](../install.md). Default installation is local stdio; request HTTP explicitly and distinguish registering an existing endpoint from installing a server on the browser host. Native versus isolated is a separate user choice.

## Install and start

```sh
uv sync --locked --extra http
# Only if explicitly choosing isolated Chromium:
uv run playwright install chromium
```

Generate a private token file **only if absent**, retaining an existing token without printing or rotating it (POSIX shell; no shell tracing). Run on the browser host; if using `BROWSER_MCP_TOKENS` instead, preserve that multi-principal configuration and do not add a conflicting single token:

```sh
umask 077
mkdir -p "$HOME/.config/browser-automation"
uv run python - <<'PY'
import os
import secrets
from pathlib import Path
path = Path.home() / ".config/browser-automation/mcp-token"
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    token = path.read_text().strip()
    if not token.isascii() or len(token) < 16 or any(c.isspace() for c in token):
        raise RuntimeError("Existing token is invalid; resolve privately, do not overwrite")
    print("Existing token retained; value not displayed")
else:
    with os.fdopen(fd, "w") as output:
        output.write(secrets.token_urlsafe(32) + "\n")
    print("Private token created; value not displayed")
PY
export BROWSER_MCP_TOKEN="$(cat "$HOME/.config/browser-automation/mcp-token")"
uv run browser-agent-mcp --transport streamable-http --host 127.0.0.1 --port 8767
# Equivalent entrypoint (same option parser):
# uv run browser-agent mcp --transport streamable-http --host 127.0.0.1 --port 8767
```

Keep that foreground process alive; it is not an automatically installed service and does not guarantee persistence across logout/reboot. Use a user-managed supervisor only when explicitly approved. The export above applies to this server shell only; it does not provision a remote client or future Codex process. Supply provider credentials only in the trusted server environment if using Luna `run`; `.env` is not loaded automatically (`uv run --env-file /private/server.env ...` is explicit). A model key is not an MCP bearer token; browser-only diagnostics/control need no model key. Tokens must have at least 16 characters; absent/invalid authentication configuration fails startup. Never commit tokens, approval stores, native profile data, or screenshots. Protect token-file permissions/Windows ACLs and environment access; do not enable shell tracing or place secret exports in global shell profiles.

For native sessions, the host operator must opt in in Chrome and set `BROWSER_NATIVE_CONSENT=1` and, if needed, `BROWSER_NATIVE_PROFILE_DIRECTORY` before server startup. Clients then call `connect_default`; isolated `launch` is an explicit alternative. File scopes and `BROWSER_APPROVALS_FILE` remain host-only policy. See [integration/approval setup](integrations.md).

## Ownership, bounds, and lifecycle

For one trust domain, `BROWSER_MCP_TOKEN` configures the default identity. For distinct principals, configure **instead** `BROWSER_MCP_TOKENS` as a private JSON object mapping 1–64 identity names to distinct tokens, for example `{"researcher":"<private-token>","reviewer":"<different-private-token>"}`. Load it from a protected environment/secret store, not literal CLI arguments. Empty/duplicate tokens are rejected; tokens must be ASCII without whitespace and at least 16 characters.

- Latest stateless protocol requests with the **same bearer identity intentionally share** that identity's persistent browser service. They are not isolated merely because two agent processes connected separately. Do not share a token between mutually untrusted agents.
- Distinct configured identities cannot list/use one another's browser session IDs. Legacy negotiated MCP sessions get their own service, bound to the authenticated owner; session IDs alone are never authentication.
- Default `--max-sessions 64` bounds SDK legacy protocol sessions; modern scopes are bounded by configured identities (at most 64). The owned-service registry reserves at most the legacy maximum plus configured identity count. `--max-browser-sessions 8` bounds browser sessions per ownership scope. `--session-idle-timeout 1800` expires idle scopes after 30 minutes, not while an HTTP GET/tool operation is in flight. Retain returned browser IDs; after expiry/restart create a new session, never guess/replay old IDs. A normal modern client disconnect retains its principal's browser state until explicit close/idle expiry.
- `--max-request-body-size 1048576` bounds **inbound** requests to 1 MiB; this is not a screenshot response-size cap. Increase only for a reviewed legitimate inbound need.
- Closing/expiring a scope, legacy session deletion, or server shutdown releases its resources. Attached user Chrome/preexisting tabs survive disconnect; owned isolated browsers close. Cancellation must not be interpreted as completed work or trigger blind consequential retries.

MCP scope isolation is not browser-profile confidentiality when the host deliberately authorizes multiple identities to attach the **same native Chrome**: all those attachments have browser-wide CDP authority to that profile. Do not grant mutually untrusted principals native access to the same Chrome. Use isolated browsers or separate server/profile deployments for that boundary.

The official SDK handles framing/lifecycle for current `2026-07-28` (stateless, no protocol sessions/GET stream) and negotiated legacy compatibility, including `2025-11-25` initialize/session/DELETE behavior. A current request's protocol metadata must match its body as required by the specification. A legacy client's session handling is not a recipe for latest-protocol requests; let the SDK/client manage transport headers. Do not use the deprecated separate `/sse` transport.

## Client connections

Securely provision the matching token on the client machine and make it available as `BROWSER_MCP_TOKEN` in the environment of the actual Codex/Claude process (not just the server). Keep values out of chat, configuration, history, and logs; preserve existing valid credentials. A remote client's `127.0.0.1` refers to itself, so use a protected tunnel or the HTTPS server address below.

### Codex

Inspect `codex mcp get browser-http --json` first. Retain an enabled equivalent URL/bearer-variable entry. For a conflicting name, preserve it and choose an unused project-specific name or obtain explicit replacement approval. Add only an absent chosen name; [install.md](../install.md#3-register-mcp-idempotently) covers the complete idempotent workflow.

```sh
codex mcp add browser-http --url http://127.0.0.1:8767/mcp --bearer-token-env-var BROWSER_MCP_TOKEN
codex mcp list
```

Equivalent secret-free `~/.codex/config.toml`:

```toml
[mcp_servers.browser-http]
url = "http://127.0.0.1:8767/mcp"
bearer_token_env_var = "BROWSER_MCP_TOKEN"
tool_timeout_sec = 180
```

`browser-http` intentionally coexists with an existing stdio server named `browser` or `browser-automation`. Do not remove or replace either automatically. If replacement is explicitly authorized, review the old entry and remove only that named entry before adding its replacement; never overwrite unrelated server configuration. Apply the same deliberate naming/scope choice in Claude.

Restart/reconnect the client as needed after configuration changes. A listed config is not proof that an authenticated tool call succeeded. Set a realistic client timeout for bounded `run`, rather than assuming long tasks can run indefinitely.

### Claude Code

Add the HTTP server without a plaintext secret, then configure its Authorization header by environment reference:

```sh
claude mcp add --transport http browser-http http://127.0.0.1:8767/mcp
```

For a project `.mcp.json` (review workspace trust prompts):

```json
{
  "mcpServers": {
    "browser-http": {
      "type": "http",
      "url": "http://127.0.0.1:8767/mcp",
      "headers": {"Authorization": "Bearer ${BROWSER_MCP_TOKEN}"}
    }
  }
}
```

```sh
claude mcp list
claude mcp get browser-http
```

The `${BROWSER_MCP_TOKEN}` text is intentional: Claude expands it from its environment. Do not substitute the actual token into the file or a `--header` command argument. Project configuration requires explicit trust/approval; avoid conflicting same-name local/user entries. The client must support the selected protocol revision; these examples are documented setup, not a claim of a paid Claude/Codex model run.

### Generic official Python SDK client

With the optional HTTP dependencies installed, save/run this on the client with its securely provisioned environment token. It discovers tools and calls the non-model diagnostic; add browser tasks only after explicit mode/consent selection.

```python
import asyncio
import os
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + os.environ["BROWSER_MCP_TOKEN"]},
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http:
        transport = streamable_http_client(
            "http://127.0.0.1:8767/mcp", http_client=http,
            max_sse_event_size=8 * 1024 * 1024,
        )
        async with Client(transport) as client:
            print(client.protocol_version)
            tools = await client.list_tools()
            print([tool.name for tool in tools.tools])
            result = await client.call_tool("doctor", {})
            if result.is_error:
                raise RuntimeError("Browser diagnostic tool failed")
            print(result.structured_content)

asyncio.run(main())
```

The v2 SDK uses `httpx2`, not old `httpx` tutorials; authentication/timeouts belong on `httpx2.AsyncClient`, not a removed `headers=` argument to `streamable_http_client`. `Client` enters/negotiates automatically; `mode="legacy"` explicitly exercises legacy initialization when needed. The client-side 8 MiB SSE event allowance permits reasonable screenshot responses without an unlimited cap; it is separate from the server's inbound body bound. Always check `is_error` and consume image blocks separately from structured/text metadata. See the [official transport API](https://py.sdk.modelcontextprotocol.io/client/transports/index.md).

## Remote deployment through TLS

Prefer a private authenticated tunnel or reviewed reverse proxy. Keep the application/CDP bound to loopback. Direct network binding requires explicit repeated `--allow-host` entries; authentication is still required. Host entries are exact authorities (`host[:port]`), not wildcard permission. An absent Origin is allowed for authenticated native clients; an Origin present on any route must exactly match a repeated `--allow-origin` entry. No arbitrary browser origins or wildcard CORS should be enabled.

Example application behind nginx:

```sh
uv run browser-agent-mcp --transport streamable-http \
  --host 127.0.0.1 --port 8767 --allow-host browser.example.com
# Only for an intentionally authorized browser-based MCP client, additionally:
# --allow-origin https://agent.example.com
```

Example nginx configuration; replace hostname/certificate paths with operator-managed values and configure firewall/TLS according to local policy:

```nginx
server {
    listen 443 ssl;
    server_name browser.example.com;
    ssl_certificate /etc/letsencrypt/live/browser.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/browser.example.com/privkey.pem;
    client_max_body_size 1m;

    location = /mcp {
        proxy_pass http://127.0.0.1:8767;
        proxy_http_version 1.1;
        proxy_set_header Host browser.example.com;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Origin $http_origin;
        proxy_set_header Connection "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-Proto "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

Use `https://browser.example.com/mcp` in clients. The app checks the actual Host/Origin, not forwarded identity/host claims. The proxy must preserve bearer authorization and Origin and must not invent an allowed Origin or strip one to bypass rejection. Disable buffering/caching for streaming cancellation; configure matching body bounds and an appropriate task timeout. Do not proxy Chrome's debugging port. TLS certificate issuance, public-network deployment and successful proxy smoke are separate evidence categories; this configuration alone proves none of them.

## Shared workflow

All transports expose the same `doctor`, session/tab, `observe`, `text`, guarded `act`, `run`, host-approved action/file and cleanup tools; screenshots remain separate MCP image blocks. Install the optional [portable skill](../skills/browser-automation/SKILL.md) for decision-first goal setting, context budgets, safe approvals and independent evidence. The skill is guidance, not an access-control requirement; server initialize instructions also guide clients that do not load skills. See [architecture](architecture.md) and [dated research/evidence](research.md).

## Exercised verification boundary

Authorized scoped checks exercised real uvicorn + official SDK clients negotiating modern `2026-07-28` and legacy `2025-11-25`, guarded fill/click with rendered `HTTP SUCCESS` and separate changed PNGs, identity/session isolation, authentication/Host/Origin/body/version rejection, cancellation/idle lifecycle, and preservation of disposable preexisting attached tabs on cleanup. A real **two-socket localhost HTTP relay** plus SDK/browser screenshot also ran; it was not nginx, HTTPS, certificate issuance or a public deployment. The documented nginx/TLS recipe remains unexercised. Initial fixture cleanup/proxy failures and mutation-scoped retests are recorded in [research](research.md); a final unified result must not be inferred by adding individual pass counts. No live Luna, personal logged-in profile, native Chrome consent dialog or paid Codex/Claude model task is claimed.
