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
