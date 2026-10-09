# Research and evidence

Primary sources accessed **2026-10-08**. Documentation can change; recheck current schemas before external API changes. These are documentation observations, not paid-provider or user-profile execution evidence.

| Source | Observed contract / relevance |
| --- | --- |
| [OpenAI Chrome extension](https://developers.openai.com/codex/chrome-extension) | Extension/native connection works with existing logged-in browser profiles. Its internal protocol is not a public interoperable attachment API; this project does not claim to implement that extension protocol. |
| [OpenAI browser](https://developers.openai.com/codex/browser), currently resolved to [ChatGPT browser](https://learn.chatgpt.com/docs/browser) | Built-in browser uses a separate profile; native extension is distinct. Full CDP access requires opt-in and exposes sensitive browser internals. Current page explicitly distinguishes available app/CLI surfaces; do not infer Codex CLI built-in browser availability. |
| [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions) | `/v1/decisions`, `gpt-6-luna`, input text plus `input_image` content, array questions with named choice/choices fields. Refusals stop the agent. Provider worker implements its own validated transport. |
| [OpenRouter Decisions reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request) | Actual alpha endpoint is `https://openrouter.ai/api/alpha/decisions`; questions and answers are maps. Choice questions use `criteria`. Generic OpenAPI server-prefix composition is not the canonical curl URL. |
| [OpenRouter multimodal Decisions](https://openrouter.ai/docs/guides/community/multimodal-decisions.md) | `openai/gpt-6-luna-decisions` supports images. `state` is a top-level array of plain strings and `{type:"image_url",image_url:{url:"data:image/png;base64,..."}}` parts. Nested images, remote image URLs, raw base64, and OpenAI `input_image` shapes are not supported. Luna maximum documented image count is 128. |
| [Jev Ultrafast source](https://github.com/browser-use/jev-ultrafast) | Dynamic indexed DOM actions and speculative operation/target heads reduce requests. Source describes stale/occlusion guards and separate field-text generation. Published timings are specific tasks/profiles and are not measurements of this project. Reported unsupported shadow roots, frames, canvas, uploads, popups, nested scrolling and keyboard widgets motivate explicit capabilities, not assumed parity. |
| [MCP Python stdio documentation](https://py.sdk.modelcontextprotocol.io/api/mcp/server/stdio/) | Stdio carries MCP protocol traffic; diagnostics must not corrupt stdout. Integration implements stdlib JSON-RPC and therefore does not install an unused MCP SDK runtime dependency. Client interoperability must be checked separately from dependency selection. |
| [Codex MCP](https://developers.openai.com/codex/mcp), resolved to [current MCP guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) | Local stdio supported; `codex mcp add NAME -- COMMAND ARGS`; config `env_vars` permits forwarding selected keys. Configuration is shared across local Codex clients. |
| [Claude Code MCP](https://code.claude.com/docs/en/mcp.md) | `claude mcp add --transport stdio NAME -- COMMAND ARGS`; project-scoped server setup requires user workspace/server approval. |
| [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) | `~/.hermes/config.yaml` uses `mcp_servers` with command/args/env. Stdio does not blindly inherit the whole shell environment; configure keys explicitly only when live provider execution is required. |


## Eight tester issues — primary research 2026-10-09

The original `issues/01..08` reports and `doc.md` are preserved. Their macOS/native Chrome observations are ground truth; local Linux fixes/fixtures are not retrospective proof that those logged-in pages now work. Active acceptance/ownership/phase and executed evidence are tracked in [`agents/release-issues.json`](../agents/release-issues.json). The previous release gates below are historical, not verification of this cutover.

| Primary source accessed 2026-10-09 | Supported contract and limits |
| --- | --- |
| [Playwright network](https://playwright.dev/python/docs/network), [request lifecycle](https://playwright.dev/python/docs/api/class-request) | Page request/response/requestfailed observation is passive; redirects correlate requests. HTTP non-2xx is a response, not a network transport failure. Prior completed requests cannot be reconstructed by starting listeners later. No body/header capture is implied by event subscription. |
| [Page.goto](https://playwright.dev/python/docs/api/class-page#page-goto) | Upstream `wait_until` supports commit/domcontentloaded/load/networkidle; this package deliberately exposes only commit/domcontentloaded/load, not network-idle as task readiness. Default upstream load is not required by this API. A navigation timeout can occur after content renders. Owned tabs survive bounded timeout; no TikTok blocking cause is inferred. |
| [Playwright WebSocket](https://playwright.dev/python/docs/api/class-websocket), [primary CDP Network schema](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json) | Playwright exposes frame strings/bytes and lifecycle; use CDP Network metadata for actual opcode, not guesses from str/bytes. Current `WebSocketFrame` properties: `opcode:number`, `mask:boolean`, `payloadData:string`; opcode1 is UTF-8 text, others base64. Despite the name, CDP describes an entire message, not necessarily an individual fragmented wire frame. Events include Created/Handshake/FrameSent/FrameReceived/FrameError/Closed. Application sockets are distinct from the CDP browser transport. No ping/pong delivery guarantee follows from a schema alone; executed fixtures must establish it. |
| [CDP isolated worlds](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-createIsolatedWorld) | Observation authority remains private per-frame isolated worlds; skipping unsupported subframes must not expand data/blob/local-file evaluation authority or disclose protected content. Native frame identity and screenshot withholding/masking are separate safety requirements. |
| [MCP progress 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/progress) | Caller requests progress in params._meta.progressToken (string/integer unique among active requests). Server notifications reference that active token, strictly increasing progress, optional message/total; omit total when unknown and stop after completion. Throttle to avoid flooding. |
| [Official SDK progress](https://py.sdk.modelcontextprotocol.io/handlers/progress/index.md) | High-level Context.report_progress(progress,total=None,message=None); client `call_tool(...,progress_callback=async(progress,total,message))`, per call not Client constructor. Real-transport slow callbacks may finish after final result; receiving before final must be exercised, not assumed from in-process tests. Surface inspected installed v2.3 low-level public `ctx.session.report_progress`; high-level examples are not a reason to guess low-level handler APIs. |
| [OpenRouter image understanding](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding.md) | Multimodal chat text helper sends text plus image_url data-URL PNG message parts; visual interpretation requires a real text-generation path, not a choice head that echoes an expected label. Separate DOM/vision provenance and self-reported confidence. OpenAI documentation access for this helper was denied403 to DecisionState; unavailable live keys prevent paid-model readback evidence. |

The implemented interfaces are documented in [browser](browser.md), [provider/state/recovery](providers.md), [external tools/progress](integrations.md) and [HTTP](http.md). Screenshot capture means the browser page viewport, not OS-screen monitoring. Default monitors exclude bodies/auth/cookies/postdata; requested bounded text payloads also require host consent and redaction is not complete privacy protection. Recovery never declares a custom Refresh click benign; operator policy narrowly governs explicit reload and cannot be supplied by a model. No live YouTube repair is claimed before accessible screenshot/DOM proof; Facebook/TikTok original reports remain unaltered and no macOS/personal-profile result is inferred from Linux fixtures.

BrowserCore also accessed [MDN display box generation](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/display#box) on 2026-10-09: `display:contents` removes the ancestor's own box without removing rendered descendant boxes. The shipped protected-subframe screenshot policy is conservative withholding (not a promise of pixel masking) when a protected frame is visible or its geometry is unknown; permitted main DOM remains available and no visual grid is returned.

Surface and ReleaseDocs independently accessed the current [OMP extension primary guide](https://raw.githubusercontent.com/can1357/oh-my-pi/main/docs/extensions.md), 2026-10-09: public `execute(_toolCallId, params, _signal, _onUpdate, _ctx)` exposes the fourth callback. The JSONL bridge can forward progress without resolving a pending result. This is a documented public signature until an actual installed OMP consumer exercises it; no paid OMP model turn is implied. Monitoring scope is explicitly `main_page_cdp_target` for application WebSockets: workers/OOP child sockets and fragmented wire-frame boundaries are not covered.

Issue08 already records the tester's successful live Luna screenshot-based REFRESH verification; the report does **not** say that the tool lacked vision. This cutover adds optional semantic readback/provenance and parent progress. Local deterministic HTTP replies prove the request/response/control contract, not new paid-model OCR quality or reading undisclosed text; current missing keys do not invalidate the tester's earlier observed success.

[Official SDK cancellation](https://py.sdk.modelcontextprotocol.io/handlers/cancellation/index.md), accessed 2026-10-09, requires awaited cleanup in `finally` under a bounded AnyIO shield (`move_on_after(5, shield=True)`); cancellation continues to the caller afterward. Modern `json_response=True` and legacy `stateless_http=True` disable handler cancellation, so those modes cannot be assumed equivalent to the exercised streaming transports. Surface's actual stdio/current/legacy SSE consumers verify progress cessation at cancellation return and after a delay while retaining the unrelated session.

### Public-page boundary and root-layout repair

BrowserCore's post-fix real public YouTube smoke reached the **logged-out** page on Linux isolated Chromium. `/tmp/browser-core-proof-final/report.json` records 12 semantic DOM targets (Search combobox/button, Guide, Home, voice search, Settings, Sign in, Shorts, Subscriptions and You) alongside `/tmp/browser-core-proof-final/youtube-independent.png`. ReleaseDocs opened that PNG: Search/navigation/sign-in and “Try searching to get started” are visibly present, matching the returned evidence. Coverage remains explicitly **partial** (`frame_unavailable`); this is not complete YouTube coverage. No video feed appeared, so visible video-card extraction, a logged-in feed, macOS/native Chrome and the tester's personal profile were not exercised. No Facebook/TikTok repair is inferred.

The generic root cause repaired is HTML root/body overflow propagation to the viewport rather than clipping rendered children against a zero-height body whose overflow is propagated. Primary source accessed by BrowserCore and ReleaseDocs 2026-10-09: [CSS Overflow3 overflow propagation](https://drafts.csswg.org/css-overflow-3/#overflow-propagation), a dated editor's draft (work in progress). Collector, action guards and frame clipping share the rule; custom/shadow/display:contents and genuinely clipping zero-size-container regressions remain independent of the public page. The earlier tester screenshot/empty DOM report remains unchanged; this public smoke establishes a narrower observed improvement, not universal site repair.

## Eight-issue release gate — 2026-10-09

After all owner slices and four concrete flow-review repairs passed, Main authorized the final unified gate at runtime commit **317b69b**. The exact command was:

```sh
env BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu \
  BROWSER_INTEGRATION_TESTS=1 uv run --extra http pytest \
  --basetemp=/tmp/browser-release-final-proof
uv build
```

Authoritative output: **152 passed in 168.66s**, wall **170.54s**, Python **3.13.12**, pytest **9.1.1**, all 14 test files; no skips or failures reported. This is one actual unified run, not a sum of scoped counts. `uv build` succeeded in **2.22s**. Host library/executable paths used for browser verification are explicit machine configuration, not portable package defaults. The preceding 67/81-test and installation gates below remain historical evidence.

### Acceptance mapping

The named tests below ran in the unified gate; each issue also has a real browser consumer rather than a source-string-only test. Commands, intermediate outcomes and owner handoffs are persisted in [`agents/release-issues.json`](../agents/release-issues.json) and the referenced owner state files.

| Issue | Regression / actual consumer | Observed result and evidence boundary |
| --- | --- | --- |
| 01 protected subframes | `test_browser_observation.py`: `test_hidden_protected_frame_keeps_parent_and_pixels`, `test_visible_protected_frame_withholds_pixels_not_parent`, `test_protected_frame_points_and_unbound_keyboard_rejected` | Hidden data/blob preserve main DOM/PNG; visible protected pixels withheld, main control retained; protected pointer/keyboard input denied while permitted parent click works. `/tmp/browser-core-proof-final/report.json`. No blanket scheme expansion or Facebook-profile claim. |
| 02 rendered semantics | `test_boxless_custom_shadow_and_clipped_semantics`, `test_body_overflow_propagates_to_viewport_not_zero_body_box`, `test_hidden_alert_descendants_not_rendered_evidence` | Local custom/shadow/boxless/root-overflow controls match independent PNG; real logged-out YouTube has 12 semantic targets matching PNG, **partial** frame coverage and no feed. Public-page limits above apply. |
| 03 slow navigation | `test_load_timeout_retains_observable_owned_tab` plus actual local HTTP partial-load consumer | Rendered page remains observable with owned ID, `navigation_status: timeout`, diagnostic and explicit cleanup. `/tmp/browser-core-proof-final/report.json`. No TikTok cause or live repair inferred. |
| 04 recovery authority | `test_page_state.py` recovery/hidden-label/nontext-policy cases; `test_nontext_drafts_require_reload_approval_and_bind_revision`; `test_agent_surface.py` real reload consumer | Narrow host-origin DOM-error reload works; default/sensitive/offscreen select/check/radio drafts pause for exact approval and preserve data without reload. `/tmp/browser-core-proof-final/nontext-draft-report.json` and Surface consumer evidence. Refresh label and vision alone do not authorize recovery. |
| 05 HTTP diagnostics | `test_real_http_status_failure_redirect_scope_and_cursor`; official SDK consumer; installed-wheel service/HTTP consumers | Browser event reports UI-hidden HTTP503 separately from requestfailed, safe redirect/timing metadata, cursor/drop/filter/scope and listener cleanup. Quiet-UI PNG + browser events under `/tmp/pytest-of-claw/pytest-1748/`; wheel real503 evidence under `/tmp/browser-release-wheel-proof/`. |
| 06 application sockets | `test_real_websocket_opcodes_inventory_late_attach_and_teardown`, `test_payload_host_consent_redaction_and_binary_metadata`; official SDK websocket tools | Real lifecycle/text/binary opcode/size, cursor/filter/history, other-tab isolation and host payload denial/redaction exercised without UI disclosure. Protocol ping/pong were **omitted by CDP**; explicit visibility limit, no invented opcode9/10. Main-page scope excludes worker/OOP sockets. |
| 07 visual freshness | `test_browser_visual.py` outside animation, changed pixels/geometry/occlusion and drag-destination cases; actual rendered canvas click | Outside animation permits verified input; changed full target crop fails closed with changed-region diagnostic; geometry/occlusion/destination guards retained. `/tmp/browser-core-proof-final/report.json` and independent PNG. No tolerance or replay. |
| 08 parent semantics/progress | `test_real_viewport_local_http_visual_recovery_consumer`, dense-grid provider regression, `test_official_sdk_incremental_progress_real_browser`, actual OMP loader/callback consumers | Real canvas PNG + unprimed multimodal HTTP request, separate vision provenance/self-reported confidence, all64grid grounding retained on dense page; stdio/modern+legacyHTTP progress before final, no extra events after cancellation, unrelated session retained; OMP three same-request updates then final. Local deterministic provider is **not live OCR/model-quality evidence**. |

### Scoped failures and repairs retained

- BrowserCore initially had **25 passed, 1 failed in 80.14s**: a clipped descendant leaked through a parent label fallback. Rendered/clipping-aware labels and root/body overflow rules were repaired, then **28 passed in 64.16s**, protected-input **1 passed in 3.87s**, and final nontext/protected-input **7 passed in 11.63s**. A first post-boxless-only public smoke still had zero YouTube targets; the generic root-overflow repair then yielded the documented 12-target public result.
- Traffic initially had **4 passed, 1 failed** because actual protocol ping/pong controls did not reach CDP. The implementation/test now reports that real transport limit rather than synthesizing events; the stable slice was **5 passed in 10.86s**. Separate outgoing/incoming text ping/pong are opcode1.
- Surface's first real integration slice was **21 passed, 3 failed in 100.59s**: a fixture button correctly required approval, so no request was made. The fixture became an explicitly safe link and asserts actual dispatch, without changing approval policy. Repaired streaming slice **8 passed in 21.13s**, SDK websocket slice **1 passed in 4.68s**, then final navigation/OMP diagnostics slice **12 passed in 27.32s**. The changed-region OMP transport case is explicitly fault-only, not fabricated browser input proof.
- DecisionState's latest agent/provider/state slice was **60 passed in 10.90s** after rendered-only error permission, progress-delivery failure handling, nontext safety and dense visual region coverage repairs. Earlier 53/56-pass outcomes remain in owner state, not misrepresented as the final slice.

### Clean installed wheel: actual browser, HTTP and stdio

Outside the checkout (`cwd=/tmp`), one secret-stripped environment installed **36 packages** into a fresh uv cache and executed installed `browser-agent doctor`, a real BrowserService consumer, modern/legacy HTTP consumers and a stdio progress consumer. Combined wall time **25.73s** is installation-plus-deterministic-smoke time, **not a live-model latency benchmark**. The invocation prefix for each was:

```sh
env -i HOME=/tmp/browser-release-wheel-home \
  PATH=/home/claw/.local/bin:/usr/local/bin:/usr/bin:/bin \
  UV_CACHE_DIR=/tmp/browser-release-wheel-cache \
  BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu \
  BROWSER_EXECUTABLE_PATH=/home/claw/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome \
  uv run --no-project \
  --with 'browser-automation[http] @ file:///home/claw/browser-automation/dist/browser_automation-0.1.0-py3-none-any.whl'
```

Commands following that prefix were `browser-agent doctor`, then `python /tmp/browser-release-wheel-smoke.py`, `python /tmp/browser-release-wheel-http-smoke.py`, and `python /tmp/browser-release-wheel-stdio-smoke.py`. These were temporary consumer harnesses (not shipped product APIs); their executed source is in the worker transcript. Doctor reported both provider keys absent and native consent false. All imported package paths resolved under `/tmp/browser-release-wheel-cache/.../lib/python3.12/site-packages`, **not the checkout**.

- Direct service launched real isolated Chromium, preserved a hidden protected frame's main evidence, filled Query=`Ada`, clicked Search and independently observed/rendered `WHEEL INPUT Ada`; before/after PNGs differed and browser events captured hidden-UI503.
- Installed HTTP entrypoint + official SDK negotiated **2026-07-28** and **2025-11-25**, each listed **21 tools**, called doctor, launched Chromium, opened/observed PNG/state, dispatched a guarded Search click, captured actual503 and application opcode1 socket traffic through all six monitoring tools, stopped listeners and closed owned browsers. Rendered `WHEEL HTTP INPUT` PNGs were opened. Ephemeral bearer credentials were never printed/persisted; no TLS/public deployment is inferred.
- Installed stdio official SDK negotiated **2025-06-18**, listed21tools, opened real Chromium and returned independent goal success via a labeled local HTTP Decisions fixture. **Three monotonic semantic progress callbacks arrived before final**, with no invented total. No paid provider or personal native profile was involved.

Machine-local evidence: `/tmp/browser-release-wheel-proof/{evidence.json,http-evidence.json,stdio-progress-evidence.json,before.png,after.png,auto-http.png,legacy-http.png}`. These ephemeral paths are not public durable artifacts and screenshots remain outside Git. Final source-gate artifacts are under `/tmp/browser-release-final-proof/`; earlier scoped artifact roots above remain separately attributable.

Archive reads confirmed wheel `browser_monitor.py`, `page_state.py`, `snapshot.js`, MCP/HTTP modules and both console entrypoints; the sdist includes the portable SKILL.md. SHA-256: wheel **3cb6112f8a2cd28c93fc1b1b81112f1980ec99a30a698aaff06df7f4b6497e72**; sdist **f499c1f3e970e049c12e32656203cfb7817727e178f3c7874df27ca5bbc56325**. Build preceded the final documentation/state-only release commit; rebuilding can change archive hashes. A targeted diff against tester baseline `5e9b55f` and protected-path status check both returned clean for `doc.md` and `issues/`.

All eight local acceptance flows are exercised with the explicit platform/provider/traffic limits above. No repaired logged-in Facebook/TikTok, macOS/native personal Chrome, logged-in YouTube recommendations, paid readback quality, desktop foreground, nginx/TLS/public deployment or competitor benchmark is claimed. Exact feature/repair commits and successful serialized pushes are tracked in `agents/release-issues.json`; the final documentation commit cannot embed its own hash and is reported in the handoff.


## Streamable HTTP and portable skills — 2026-10-09 research

Primary URLs accessed **2026-10-09**; these contracts are documentation research, not executed deployment/client/model evidence.

| Source | Actual current contract and compatibility boundary |
| --- | --- |
| [Latest MCP transports](https://modelcontextprotocol.io/specification/latest/basic/transports), [2026-07-28 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) | Current HTTP revision removes protocol sessions and GET stream. One POST per message; Accept includes JSON and SSE. Each request has MCP-Protocol-Version matching body `_meta` protocolVersion, Mcp-Method matching method, and applicable Mcp-Name matching tool/resource/prompt name. Header mismatch/unsupported version are rejected. Request SSE disconnect cancels that request; old cancellation notifications are not the current HTTP mechanism. Origin must be validated. |
| [2025-11-25 transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | Legacy initialization/session IDs/GET or405/DELETE and cancellation differ. A session ID is not authentication. Preserve negotiated compatibility without claiming the legacy tutorial is latest. |
| [Official SDK index](https://py.sdk.modelcontextprotocol.io/llms.txt), [ASGI](https://py.sdk.modelcontextprotocol.io/run/asgi/index.md), [legacy clients](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/index.md) | v2 MCPServer/low-level Server ASGI app routes modern and legacy protocol eras; SDK owns framing, initialization and stream lifecycle. Legacy sessions are in-process and require stickiness across workers. This feature intentionally runs one process with application-owned bounded browser scopes. The existing stdlib stdio implementation remains independent of optional HTTP dependencies. |
| [SDK Client transports](https://py.sdk.modelcontextprotocol.io/client/transports/index.md), [Client](https://py.sdk.modelcontextprotocol.io/client/index.md) | `Client(transport)` async context negotiates; `streamable_http_client(url,http_client=...)` uses supplied `httpx2.AsyncClient` for Authorization and timeouts. No old transport `headers=` keyword. `mode='legacy'` forces initialize-era compatibility. Results expose `structured_content`, `is_error`, and separate image blocks. Current default SSE event cap1MiB may need bounded adjustment for screenshots. |
| [Codex MCP](https://developers.openai.com/codex/mcp) → [current guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) | Documents Streamable HTTP bearer support; config `url`, `bearer_token_env_var`, `tool_timeout_sec`; CLI `mcp add --url`. Reads initialize instructions, with first512 characters self-contained recommended. Product support is not a paid-model/tool-execution claim. |
| [Claude Code MCP](https://code.claude.com/docs/en/mcp.md) | `claude mcp add --transport http NAME URL`; JSON `type:'http'`/`streamable-http`, `url`, `headers`. `.mcp.json` supports `${VAR}` header expansion; use a custom MCP token variable, never provider credential references. Workspace/server trust still applies. |
| [Agent Skills specification](https://agentskills.io/specification) | Directory containing SKILL.md with YAML `name` matching dirname (lowercase/digits/hyphens,1–64), nonempty `description`≤1024; optional compatibility≤500. Progressive disclosure; body under500lines recommended. Instruction-only skill needs no scaffolding/scripts. Reference validator command `skills-ref validate PATH`. |
| [Codex skills](https://developers.openai.com/codex/skills) → [Build skills](https://learn.chatgpt.com/docs/build-skills) | Personal `~/.agents/skills`, repository `.agents/skills` scanned from CWD to root, supports symlink folders; explicit `$browser-automation` and `/skills`, implicit description matching. |
| [Claude Code skills](https://code.claude.com/docs/en/skills) | Personal `~/.claude/skills`, repository `.claude/skills`, `/skill-name`; standard plus product extensions. Symlinked folders documented. Cloud/Cowork do not simply read machine-local personal skills. |
| [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | Default profile primary `~/.hermes/skills`; installed skills become slash commands; skills_list/skill_view progressive disclosure. Named profiles/external directories follow Hermes configuration. No invented universal installer/discovery directory. |

The portable skill is standalone and uses discovered logical tool names regardless of client namespace. It guides goal/acceptance/scope, explicit browser mode, persistent IDs, Luna decision-first bounded `run`, context/privacy budgets, exact host approvals, stale rejection, independent rendered outcome evidence and safe stop/cleanup. Its Facebook shortlist/draft example is not an exercised benchmark. Native consent and provider credentials are server-side and distinct from MCP bearer tokens. The existing OMP extension remains stdio/JSONL; no HTTP capability is inferred there. TLS/proxy configuration is an operator recipe, not evidence of deployed HTTPS. Actual HTTP and skill verification must be recorded after the coordinated authorization gate.

Installed-client schema inspection on 2026-10-09: `codex mcp add --help` executed successfully and lists `--url <URL>` for Streamable HTTP plus `--bearer-token-env-var <ENV_VAR>` (HTTP-only). This confirms the documented command shape, not authenticated connection, skill discovery, tool execution or paid-model behavior. Claude `${VAR}` Authorization expansion above is primary-documentation evidence; no installed Claude invocation is inferred.

### Authorized portable skill verification

After Main's coordinated gate authorization, the official reference validator ran:

```sh
uvx --from 'git+https://github.com/agentskills/agentskills.git#subdirectory=skills-ref' \
  skills-ref validate skills/browser-automation
```

It returned **Valid skill**; uv resolved the reference tool at upstream commit `69ef37e9424c0a7ea9dd2293b559e43ec8176379`. A separate structural smoke checked required frontmatter/name/description/compatibility bounds, the 48-line SKILL.md, zero external local skill-resource dependencies, and existence of 21 local documentation link targets. It did not pin source wording or evaluate model compliance.

The exact documented installation shell block also executed successfully in a separate temporary HOME: Codex/Claude symlinks and Hermes's guarded copy contained the intended SKILL.md; running it again preserved all existing destinations; replacing the Codex destination with a deliberately broken symlink and rerunning preserved that symlink too. No real user HOME was modified. Installed-client inspection found Codex and Claude binaries, but no Hermes binary; Claude's `--help` exposed no non-model skills-list subcommand, so no paid invocation was attempted to manufacture discovery evidence.

Actual installed **Codex app-server** discovery also passed: a temporary project `.agents/skills/browser-automation` symlink to the source skill, isolated temporary HOME/CODEX_HOME, and secret-stripped child environment were used. Only `initialize`, `initialized`, and `skills/list` (`cwds`, `forceReload:true`) were sent; the response returned `browser-automation`, `enabled:true`, `scope:repo`, and the correct source path. No thread/turn or paid model was invoked, and user configuration was unchanged. Machine-local evidence is `/tmp/browser-skill-proof-qjbtxeun/skills-list.json`. Protocol source: [Codex app-server](https://developers.openai.com/codex/app-server), accessed 2026-10-09. This proves that installed Codex discovers the skill, not that an agent follows every instruction. Actual installed Claude/Hermes discovery remains unexercised; their paths/invocations are documentation-based.


### Authorized scoped HTTP verification

HttpTransport's executed scoped checks (recorded in `../agents/http.json`) were:

| Check/run | Actual outcome |
| --- | --- |
| `uv run --extra http pytest -q tests/test_mcp_http.py` | 4 passed in 2.34s: ASGI authentication/security/protocol boundaries. |
| Initial real HTTP + existing stdio slice | Existing stdio 5 passed; six HTTP cleanup fixture failures in the 42.22s run expected exit 0 from uvicorn after SIGTERM, which returns -15 after graceful shutdown. Browser/lifecycle assertions reached cleanup; fixture now requires `Application shutdown complete` and no ERROR logs before accepting -15. |
| Expanded/repaired HTTP cases | 9 passed, 1 reverse-proxy fixture failure in 41.11s: relay rewrote only the first keepalive request's Host. |
| Corrected relay `Connection: close` | Actual two-hop localhost proxy case 1 passed in 5.53s. |
| Explicit negotiated-protocol evidence | Modern/legacy guarded browser and real proxy subset 3 passed in 12.96s. |
| Same-bearer legacy-session isolation | Legacy guarded browser case 1 passed in 5.87s. |

All ten actual HTTP browser cases were exercised successfully across these mutation-scoped runs, **not** one invented “10 passed” output. No production HTTP implementation repair was needed during that scoped gate. Official SDK + uvicorn actually negotiated modern **2026-07-28** and legacy **2025-11-25**; filled Name=`Ada`, clicked, and observed rendered `HTTP SUCCESS` with separate PNG image blocks changing from 9,112 to 10,992 bytes. Checks covered distinct identities and same-token distinct legacy sessions, missing/wrong bearer 401, Origin 403, Host 421/forwarded Host rejection, body 413, invalid version 400, modern SSE disconnect and legacy cancellation, active-operation idle protection, expiry/DELETE/shutdown preservation of disposable attached browser/preexisting tabs. A same-principal modern disconnect retained browser state, as designed.

Scoped machine-local artifacts: modern `/tmp/browser-http-protocol/test_official_client_browser_i0/http-2026-07-28-evidence.json` plus before/after PNGs; legacy `/tmp/browser-http-legacy-isolation/test_official_client_browser_i0/http-legacy-evidence.json` plus PNGs; proxy `/tmp/browser-http-protocol/test_actual_localhost_reverse_0/reverse-proxy-evidence.json` and `reverse-proxy-browser.png`. Main opened the modern rendered PNG (`HTTP SUCCESS`, Name=`Ada`) and metadata confirming cross-principal `unknown_session`; proxy metadata records two real HTTP hops and `tls:false`. The proxy is a real localhost relay, **not nginx or TLS**. No public HTTPS deployment, personal logged-in profile/Chrome native consent dialog, live Luna/provider, or paid agent turn is inferred.

Implementation milestone `25e4095db09e87d5b52d03880f78057a6b18dd41` and scoped fixture/evidence milestone `c5b8c9347bc5cb9aac00fef407addb28996deac8` were successfully pushed to `origin/main` by DocsSkill using exact handed-off HTTP paths. Portable skill milestone `d2dd45e96ddfa2d5ef9a8c02d400764b4ad2bb10`, client/deployment docs `e01f349f91084b4208e6fae4be8bf064e54fc09b`, and skill-validation evidence `d8458bfb9f9e5076971a742f12672614eaaabaf2` were also successfully pushed. The final unified gate is recorded separately rather than inferred from these scoped counts.

### Final HTTP/skill unified gate — 2026-10-09

After both workers' scoped results stabilized, Main explicitly authorized the final unified suite and package/installed-wheel checks. Runtime source/fixtures were at pushed commit `c5b8c9347bc5cb9aac00fef407addb28996deac8`; subsequent edits are documentation/state only.

```sh
env BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu \
  BROWSER_INTEGRATION_TESTS=1 uv run --extra http pytest \
  --basetemp=/tmp/browser-http-final-proof
uv build
```

The authoritative unified output was **81 passed in 165.02s** (wall 166.54s), Python 3.13.12/pytest 9.1.1, all nine test files and no reported skips/failures. This includes all four HTTP security cases, all ten HTTP real-browser cases together, the existing stdio/CLI/OMP cases and earlier browser/provider/agent guards. Host browser libraries were explicitly configured; this path is not a portable package default.

`uv build` succeeded in 1.61s, producing the 0.1.0 sdist/wheel. Archive inspection confirmed `skills/browser-automation/SKILL.md` (6.8KiB) in the **sdist**, and `browser_automation/snapshot.js` (7.9KiB), `mcp_http.py`, plus console entrypoints `browser-agent`/`browser-agent-mcp` in the **wheel**. The portable skill need not be installed inside a Python wheel's import package. Artifact SHA-256: sdist `8263d8b25ba7ea0d7b4091b540f84fe53805b7dd9f252ca0b4df08124a163d1c`; wheel `a320ae9089846ae47cd4b83fa063d58668790465907adc72ac5aaa5ed632aca7`. Artifacts were built before the final documentation/state release commit; rebuild hashes may differ.

Clean installed-wheel command, executed from `/tmp`:

```sh
env -i HOME=/tmp/browser-http-wheel-home \
  PATH=/home/claw/.local/bin:/usr/local/bin:/usr/bin:/bin \
  UV_CACHE_DIR=/tmp/browser-http-wheel-cache \
  uv run --no-project \
  --with 'browser-automation[http] @ file:///home/claw/browser-automation/dist/browser_automation-0.1.0-py3-none-any.whl' \
  browser-agent doctor
```

It passed (wall4.97s; installed35packages): Playwright available, provider keys absent, no sessions, native consent false. In the same isolated wheel environment, an inline no-project Python probe generated an ephemeral bearer token without printing it, started the actual installed `browser-agent-mcp --transport streamable-http` entrypoint on a free loopback port, and used official SDK `Client(streamable_http_client(...,http_client=httpx2.AsyncClient(...)))` in auto and legacy modes. Both listed15tools, returned server instructions and successful `doctor`, and negotiated **2026-07-28**/**2025-11-25** respectively. Package `__file__` resolved under `/tmp/browser-http-wheel-cache/.../lib/python3.12/site-packages`, **not the checkout**. Graceful shutdown logged `Application shutdown complete` with no ERROR and exit-15 (expected uvicorn SIGTERM semantics). No model key, native consent, browser-library override or checkout imports were supplied; **installed-wheel browser launch was not exercised**. Evidence: `/tmp/browser-http-installed-wheel-proof.json`. This validates installed HTTP startup/client transport, not only package imports and not a paid Codex/Claude invocation.

Unified real-browser artifacts are machine-local under `/tmp/browser-http-final-proof/`; scoped and Codex discovery artifacts above remain separate. No live Luna/provider call, personal Chrome profile/native consent dialog, headed desktop, nginx/TLS/public deployment, actual Facebook benchmark or matched competitor benchmark is claimed. Skills-ref and actual installed Codex skill discovery are verified; installed Claude/Hermes skill discovery remains documentation-based. The final documentation/state commit cannot embed its own hash without changing it: the hand-off reports that exact commit and push result.

## Agent-directed installation contract — 2026-10-09

Primary sources accessed 2026-10-09: [Codex MCP](https://developers.openai.com/codex/mcp), [Codex skills](https://developers.openai.com/codex/skills), [uv installation](https://docs.astral.sh/uv/getting-started/installation/), and [uv locking/syncing](https://docs.astral.sh/uv/concepts/projects/sync/). Codex supports CLI stdio/HTTP registration and user/project skill discovery; a skill is optional instructions, not an automatically enforced policy. Local stdio needs no persistent server daemon.

Installed interface introspection (not a model task or installation smoke): `codex-cli 0.156.1`, `uv 0.11.16`; `codex mcp add NAME -- COMMAND...`, `codex mcp get NAME --json`, and HTTP `--url URL --bearer-token-env-var ENV` are present. `--env KEY=VALUE` is stdio-only. `codex app-server generate-json-schema --out /tmp/browser-setup-codex-schema` generated the actual installed JSON-RPC schemas: initialize requires `clientInfo.name`/`version`; `skills/list` accepts `cwds` and `forceReload`; `mcpServerStatus/list` exists. No paid inference is needed for skill discovery.

uv's current source documents reviewed standalone scripts, package managers, `pipx` or `pip`; downloading and immediately executing an unreviewed installer is not required. `uv sync --locked` installs from the project lockfile and fails instead of updating an out-of-date lock. Extras need `--extra http`; `uv run` otherwise automatically locks/syncs. Installation preserves an existing `.env`, unrelated Codex configuration, foreign checkouts and skill destinations; HTTP client bearer environment and server token provisioning are distinct responsibilities. Provider-key presence and browser consent are readiness categories, not prerequisites for tool diagnostics.

### Authorized fresh-checkout installation proof

Main authorized the documentation-only gate after both workers were READY and the early docs commit was pushed. Executed `/home/claw/.local/bin/uv run --no-project --with mcp==2.3.0 python /tmp/browser-agent-install-proof.py` from `/tmp`, using isolated `HOME`/`CODEX_HOME`, a fresh HTTPS remote clone of pushed commit `3f3cfc5d9a7eec1480c513decae53671c10c813c`, and the runbook's actual sync/registration/skill/doctor commands. The successful complete run took **10.98s**; machine-local evidence is `/tmp/browser-agent-install-fmtf6879/report.json` and `skills-list.json`. The fixture's installation path is intentionally temporary; the documented user default remains stable `$HOME/.local/share/browser-automation`.

| Exercised category | Observed outcome |
| --- | --- |
| Local installation | Actual `uv --directory ABS sync --locked`; private create-only `.env`; CLI doctor succeeded without model keys or browser consent. |
| Codex MCP configuration | Actual `codex mcp add/get --json` saved enabled `browser-automation`, absolute uv/check-out/env-file arguments, and stdio transport; unrelated model/MCP fixture settings including a fake private environment value were preserved. |
| Reinstallation | Equivalent MCP registration and correct skill symlink retained; existing `.env` containing a fake private value and full config retained byte-for-byte. No real user credentials were used. |
| Actual stdio protocol | Official SDK `Client(..., mode="legacy")` negotiated **2025-06-18**, listed **15 tools**, and successfully called `doctor`. The published Python diagnostic was also extracted from the fresh clone's `install.md` and executed verbatim as a temporary file with the documented `uv run --with mcp==2.3.0` command. |
| Actual Codex skill discovery | Installed `codex app-server --stdio` initialize and `skills/list` returned exactly one enabled `browser-automation` with **user** scope and a resolved `SKILL.md` path inside the fresh checkout. No model turn was started. |
| Conflicts | Foreign MCP registration retained and unused `browser-automation-local` selected; subsequent equivalent alternate entry retained. Broken foreign skill symlink rejected unchanged. A real foreign-origin Git checkout was rejected with its user-work marker unchanged. |
| HTTP opt-in branch | Actual `sync --locked --extra http`, private 0600 ephemeral token, foreground loopback server and Codex URL/bearer-environment registration; no token in TOML/logs. Official SDK authenticated against that endpoint, negotiated **2026-07-28**, listed **15 tools**, and returned successful `doctor`. Foreground server stopped after the probe; no permanent service installed. |

Both protocol diagnostics reported Playwright available, both provider-key flags false, no sessions and native consent false. Codex emitted a temporary-directory helper/PATH-alias warning but completed configuration/discovery; this is not a deployment blocker or permission workaround. The initial proof attempt completed stdio, HTTP, skill discovery and first rerun but stopped on a **throwaway probe bug**: the later skill-conflict setup incorrectly required an already-equivalent alternate MCP name to be absent. Only `/tmp` probe logic was repaired to retain equivalent selected names; the entire fresh-clone proof then passed. No runtime source or documented recipe failure was suppressed.

An additional exact published-code rerun (`uv run --no-project python /tmp/browser-agent-private-file-proof.py`, **0.55s**) executed the existing-file branches from `install.md` and `docs/http.md` against the same fixture. Both printed only retained/value-not-displayed notices; `.env` and the HTTP token retained identical bytes and **0600** permissions. Evidence: `/tmp/browser-agent-install-fmtf6879/private-file-rerun.json`. No token value or fake private `.env` value was included in the report.

Limits: existing installed uv/Codex and shared uv/managed-Python caches were used, so this is fresh-home/fresh-checkout installation evidence, **not clean-machine prerequisite provisioning or an autonomous Codex install conversation**. No paid provider call, native/isolated browser launch, personal profile, current installing conversation tool refresh, remote/TLS deployment or persistent service was verified. No build, lint or full browser suite was run for this documentation-only change.

## What is measured

No live model calls or native logged-in Chrome sessions were exercised during bootstrap. No comparative benchmark exists. Installation, unit tests, real-browser fixtures, native attachment, and live provider requests are separate evidence categories; subsequent verification records must name what actually ran and what was unavailable.

Performance reports must separate:

1. Browser-only snapshot/action timings without a model.
2. End-to-end deterministic local-provider agent loops (real browser, synthetic policy).
3. Live paid-provider end-to-end loops, naming provider/model and including network, field text, retries, waits, and independent verification.

Include initial-state boundaries, task, environment, repetitions, errors, outcome evidence and raw artifacts. Screenshots establish rendered browser state, not provider correctness or superiority. Do not claim a winner against Jev/Codex without matched data.

## Verification evidence — 2026-10-08

The Decision worker's authorized scoped verification initially reported 21 offline passes and one real-browser launch failure caused by missing host shared libraries. After explicit host library configuration, its real-browser deterministic HTTP-provider scenario passed (`1 passed in 3.31s`). This is **not** a live Luna request or a comparison benchmark.

The rendered PNG and DOM outcome show the Query field filled with `hello` and visible `Submitted: hello`. The agent returned `success` only after a separate verification call. Recorded request metadata shows five actual loopback HTTP requests: three joint action choices, one structured field-text request, and one goal-verification request; each included one image part. The fixture supplied deterministic responses despite model identifiers in payloads.

For this single local fixture run, recorded agent metrics were browser work **1,203.12 ms**, local-provider work **57.91 ms**, and total agent elapsed **1,281.72 ms**, with zero stale reobservations. These are instrumentation categories within one deterministic task, not standalone browser microbenchmarks, paid-model latency, headed-desktop proof, or general reliability evidence. The worker ran the scenario with explicit `BROWSER_AGENT_LIBRARY_PATH` deployment configuration; installation of host libraries is separate from package functionality.

Original evidence stays local and untracked under `/tmp/browser-decision-proof-configured/test_real_browser_local_determ0/`: `deterministic-agent-outcome.png`, `deterministic-agent-outcome.json`, and `deterministic-agent-request-evidence.json`. Parent and Harness opened the rendered PNG and outcome metadata. The final unified suite and installed-wheel doctor subsequently passed as recorded below; no native personal-profile or live-provider success is claimed.

### Browser-only fixture and attached-session checks

The Browser worker reports **16/16 scoped tests passed in 33.24s**, followed by `test_mcp_attached_browser_preserves_preexisting_tab` passing in **5.71s**. This attachment test uses a controlled browser fixture, not the unavailable personal logged-in Chrome profile.

Harness opened `/tmp/browser-engine-proof/browser-proof.png` and `.json`: the real headless Chromium page visibly contains `Rendered DOM proof` in the Name field and `Canvas clicked`, with shadow/frame controls rendered. The saved browser-only metadata records screenshot observation **642.63 ms** and visual-target click **7.47 ms** for that single fixture run; these are not general benchmark medians. Its `live_provider` flag is false. Headless rendered screenshots are not proof of an interactive headed desktop, personal login session, or paid-model execution.

### CLI, official MCP client, and installed OMP integration

The Integration worker's final scoped command was `BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu BROWSER_INTEGRATION_TESTS=1 uv run --with mcp pytest tests/test_service.py tests/test_service_stdio.py tests/test_service_omp.py --basetemp=/tmp/browser-integration-final-proof`, reporting **27 passed in 16.37s**. The SDK is a temporary verification dependency, not a runtime requirement of the stdlib MCP server.

Local evidence under `/tmp/browser-integration-final-proof/` includes real CLI before/after PNGs, official-MCP-client rendered-action/drag/selection/upload/policy PNGs, a controlled attached-session PNG, and `test_actual_omp_extension_load0/omp-proof.json`. Harness opened `mcp-after.png` showing `Rendered SUCCESS`, and OMP metadata showing the installed host registered `browser_agent` and completed the `browser-automation.doctor` probe with `agentInvoked: false`. This proves local tool registration/execution, not a paid OMP model task. The final unified gate below exercises all test files together, separately from these scoped runs.

Fixture secrets, approval records, probe scripts and raw screenshots remain local/untracked. No real personal-profile, live Luna, CAPTCHA/detection-bypass, or matched competitor benchmark claim follows from these tests.

### Final unified verification gate

On **2026-10-08**, HarnessFinal exercised the following against source commit `33444dd8d0e928d1a60bad4507c4d97c8aac7057` (later changes are documentation/state only):

| Check | Exact exercised outcome |
| --- | --- |
| `BROWSER_AGENT_LIBRARY_PATH=/home/claw/.local/lib/chromium/usr/lib/x86_64-linux-gnu BROWSER_INTEGRATION_TESTS=1 uv run --with mcp pytest` | **67 passed in 57.77s**, wall 59.26s; Python 3.13.12, pytest 9.1.1; all seven test files ran with no skips/failures reported. |
| `uv build` | Built `dist/browser_automation-0.1.0.tar.gz` and `dist/browser_automation-0.1.0-py3-none-any.whl`, wall 1.86s. |
| Wheel resource inspection | Archive reader confirmed `browser_automation/snapshot.js` (7.9KB). `unzip -l` could not run because `unzip` is absent (exit 127); the archive inspection supplied equivalent resource-presence evidence. |
| `uv run browser-agent doctor` | Passed, wall 0.40s: Playwright available, both provider keys false, no sessions, native consent false. |
| Clean installed-wheel doctor | Passed from `/tmp`, wall 3.34s, same diagnostic state; new uv cache installed 11 packages. No checkout/project, provider credentials, browser-library overrides, or native consent supplied. |

Clean-wheel command:

```bash
env -i HOME=/tmp/browser-final-clean-home \
  PATH=/home/claw/.local/bin:/usr/local/bin:/usr/bin:/bin \
  UV_CACHE_DIR=/tmp/browser-final-wheel-cache \
  uv run --no-project \
  --with /home/claw/browser-automation/dist/browser_automation-0.1.0-py3-none-any.whl \
  browser-agent doctor
```

Wheel SHA-256: `ccf2d99fed89f495de1ba2003006ebd7736776e6c6c20b5516a888a9ce01cc01`. Sdist SHA-256: `0ff7aacb7bfc59cdafae6ec13adfdc01356428bb67a8e5f5a286983cc849acc0`. These identify the artifacts built before this documentation-only release commit; rebuilding later can produce different artifact hashes.

The local gate summary is `/tmp/browser-final-unified-verification.json`. Unified-run fixture artifacts are under `/tmp/pytest-of-claw/pytest-1743/`, including `test_real_browser_local_determ0/deterministic-agent-{outcome.png,outcome.json,request-evidence.json}`, `test_mcp_official_client_real_0/{mcp-before.png,mcp-after.png,mcp-dragged.png,mcp-cleared.png,mcp-policy-guarded.png,mcp-uploaded.png}`, and `test_actual_omp_extension_load0/omp-proof.json`. These paths are machine-local, untracked and temporary, not guaranteed durable public downloads. They are distinct from the earlier scoped artifact paths above; prior timing figures were not remeasured as benchmarks by this gate.

Main reports the focused read-only security rereview completed with **zero unresolved high/medium findings**, resolving isolated CDP-world authority, prohibited URL-scheme checks, and POSIX dirfd-pinned `ScopedFiles` boundaries. Its evidence is `agent://SecurityReview/findings`. The final suite passed the adversarial real Chromium fixtures in `tests/test_browser_context.py`. This is source-review plus tested-boundary evidence, not a blanket security guarantee.

**Explicitly unverified:** live Luna/OpenRouter/OpenAI paid calls (no live keys); attachment to a personal logged-in Chrome profile (none attached or hijacked); headed interactive desktop operation; installed-wheel browser launch; CAPTCHA/detection bypass; and matched Jev/Codex performance. The controlled attached-browser test exercises fixture preservation rather than a user's profile. Official MCP SDK 2.3.0 interoperability and OMP local tool execution do not prove every client/version or a model-driven OMP task.

### Exact commit ledger

The following ordered repository history identifies implementation and evidence milestones. Commits through `33444dd` were already pushed to `origin/main` before this gate. `d681058` records the remaining Integration-owned documentation/state; it is pushed together with the final documentation verification commit. The latter cannot embed its own Git hash without changing that hash: the final hand-off reports its exact hash and successful push result.

```text
91750ba341383758820889c9f7e6fc15b6394f80
9e2fa1636b1e4bca5e790005638c5f1f45f03fb1
87a69521d0cd606fd58e9ae8eff532966c389d95
921b8c2490ad6a1be3947fe07ae40ea2480a3ff9
8a4e12cbaf806850a54c22e876663a495c5b5ac2
ff8e6b3159967ea315fcfa365040488cbb2b1581
f4919b764bce3ce7f416485fbdd47874a690d772
95d8094503939598b26a2b9db73d3756feedb95d
a62732257147f700eb4cc087fb483de184c29e96
8716a250722a8f1427e7e07916696b38fe587f16
035632596d3517a2e6b608efc1f8a9e97642b429
76f8c224598fc70cfd188ad7de46581e92577074
a0866988f2a45b484f4aacc027344b20008cb49b
3839daf6054c781962dc9e468b8160173fc8291a
60aed36ebc4aee108c47d43140a0704fa2ce7c8c
833ab9275c4388ae4707ed0cefb6b226ebb5653f
4d5fa9735a9ce2acee87a257da4285eb81604064
6e959ba4d4202e29d14faed9cd4c256f455cd123
66d194c0994412c3d51d592dd784702979df8d2d
59259574a32ac277beb01e6998059dfc66e81ead
75fe69b6e5a99da383efb2c8bef6db3733934347
01a2b64b42af9806551bde3c1586b13ef0b8b4b6
535cc9494fb66c85f990c4f3d45d659886daf898
0255f3766893fe26e93abf657da0d634f033063b
2132ee44bd02f2a90bcfa49cae7846303f1b7a83
08608562393860a2d6e01f453afeb30be49aeb49
251d9faae90d7f51447f397b0ea709d1378cb7e0
160bd4a7b8a5cd8f758e493e42bff839956ca085
75342d7c4a32bc62483a40d1cbd351e81a6ed742
33444dd8d0e928d1a60bad4507c4d97c8aac7057
d6810587e50497f89d1b328673e79a69bb8dfb1c
```
