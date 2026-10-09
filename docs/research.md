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

Actual installed **Codex app-server** discovery also passed: a temporary project `.agents/skills/browser-automation` symlink to the source skill, isolated temporary HOME/CODEX_HOME, and secret-stripped child environment were used. Only `initialize`, `initialized`, and `skills/list` (`cwds`, `forceReload:true`) were sent; the response returned `browser-automation`, `enabled:true`, `scope:repo`, and the correct source path. No thread/turn or paid model was invoked, and user configuration was unchanged. Machine-local evidence is `/tmp/browser-skill-proof-qjbtxeun/skills-list.json`. Protocol source: [Codex app-server](https://developers.openai.com/codex/app-server), accessed 2026-10-09. This proves that installed Codex discovers the skill, not that an agent follows every instruction. Actual installed Claude/Hermes discovery remains unexercised; their paths/invocations are documentation-based.


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
