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

Original evidence stays local and untracked under `/tmp/browser-decision-proof-configured/test_real_browser_local_determ0/`: `deterministic-agent-outcome.png`, `deterministic-agent-outcome.json`, and `deterministic-agent-request-evidence.json`. Parent and Harness opened the rendered PNG and outcome metadata. Unified full-suite and installed-wheel verification remain pending; no native personal-profile or live-provider success is claimed.
