# Changelog

## 0.1.0 — Unreleased

Autonomy round (2026-10-10/11):

- `687cf22` Add `navigate` for session-owned tabs: a typed `not_owned_tab` refusal for preexisting user tabs, `new_tab` URL rules and a tab kept on timeout. Add `act` `settle_ms` with a `navigation {started, status, url}` settle signal that is never a success claim. Fix the macOS 64 KiB stdout line limit in the stdio test harness.
- `481041a` Revalidate an observation before minting an approval, so stale revisions fail before a pending approval exists. Drop a tab's cached observations and pending actions after input. Flag covered elements with `covered: true` and no operations, using the input guard's hit test. Use the largest visible fragment as the input point of a wrapped inline link. Overlay changes now invalidate older observations.
- `5aae8c3` Make `observe` compact by default (`detail: "compact"|"full"`, `visual_regions`, `text_scope: "document"`). Compact elements carry id/role/name/ops plus non-default state, and same-origin hrefs are relative. The service cache keeps full data, and `run` observations are compacted. Output was about 3x smaller on single real-page runs.
- `cb059ae` Add the host approval mode `BROWSER_APPROVAL_MODE` (`strict` | `standard` default | `autonomous`) with tiers none/ordinary/consequential/critical in `approval_policy.py`.
  - `strict` reproduces the legacy decisions and reasons exactly.
  - Critical actions (payment controls, sensitive input, account deletion, credential-header edits) always need a per-action host token.
  - Auto-approved actions carry an `approval` audit.
  - `navigate`, network, upload, download and `run` are wired to the policy.
  - Risk matching is whole-word or URL-segment, which avoids false positives on content links.
- `3e598bb` `observe` retries only its own read-only `stale_observation`: at most 3 attempts within ~1.5 s, reported as `settle_retries`, with a `page_settling` diagnostic when retries run out. `act`, `check_observation` and file transfers stay fail-fast.
- `1d1b9cd` Wheel `scroll` waits (≤1.5 s) for the window and scrolled containers to stop and reports `scroll {settled, moved}`. Bound the site/external-Chrome test fixtures so they cannot hang the suite.
- Unified macOS gate at `e8d79f9`: **273 passed, 1 deselected in 141.61s**, and `uv build` succeeded. The deselected OMP extension-load test needs an `omp` with `--no-ui`. Three attached-external-Chrome tests were not rerun after `1d1b9cd` because the screen was locked; this is an open blocker. No personal native Chrome, paid model or competitor comparison was exercised. Release docs, the AGENTS.md approval wording and agent state are updated in the following docs commit.

- Add actual network query/header/payload/response-body inspection with selectable sensitive output, transparent byte/base64 chunking and explicit retention/unavailable diagnostics; keep private replay credentials out of model previews.
- Add browser-cookie-context API call/replay across authorized HTTP(S) endpoints and methods, immutable exact-approved plans for consequential requests, ordinary same-origin safe-read dispatch, strict host approval mode, manual redirect credential boundaries and separate API-response provenance.
- Add explicit multi-tab capture and future-tab/pop-up opt-in, plus detail/body/call/replay/execute and bounded issued-call discovery through shared CLI, MCP stdio/current+legacy HTTP and OMP tools; discover the catalog dynamically instead of requiring an obsolete fixed count.
- Preserve retriable body-read history and recover finished/evicted captured response bytes from retained browser handles without HTTP resend; preserve sanitized relative redirect Locations, bind plans to document generations and keep one authoritative executor plan lifecycle.
- Verify the complete network cutover with one authorized **196-test** unified gate (no reported skips/failures), successful build and **8 clean installed-wheel real-browser/client workflow/cancellation cases** through stdio/current+legacy HTTP/CLI; include actual OMP cancellation in source acceptance, independent parent PNG/server-ground-truth review and transparent privacy/provenance/SDK-buffering limits.

- Add bounded navigation that preserves owned tabs on timeout, safe skipped-subframe diagnostics and independent DOM coverage; improve generic custom-element/shadow/display:contents collection without expanding protected-document authority.
- Replace whole-viewport visual equality with exact target-local pixel guards while retaining semantic/geometry/occlusion checks and fail-closed changed-target diagnostics.
- Add scoped bounded HTTP and application-WebSocket monitors with cursor/history metadata, HTTP status/failure distinction, true frame opcodes and safe metadata defaults; text payload capture requires explicit host-and-call opt-in.
- Add DOM page-state/recovery recommendations, separate opt-in multimodal visual summaries, snapshot-bound host-controlled reload and incremental request-scoped progress through CLI/MCP/HTTP/OMP; retain exact consequential-action approvals and cancellation ownership.
- Verify the eight-issue cutover with one authorized **152-test** unified gate (no reported skips/failures), real rendered consumer/screenshot evidence, current/legacy HTTP and stdio progress/cancellation, actual OMP callbacks and a clean installed-wheel Chromium/monitoring/HTTP smoke. Record initial scoped failures and repairs, CDP control-frame/worker-scope limits, and only a partial logged-out YouTube improvement (12 semantic targets, no feed); preserve tester reports and avoid live-profile/model-quality claims.

- Add an agent-followable `install.md` entrypoint and prominent natural-language installation prompt: configure the local stdio MCP and portable skill safely, preserve existing credentials/configuration, distinguish diagnostics from live-model/browser readiness, and make HTTP an explicit deployment choice rather than an installer daemon.
- Verify the agent-install runbook from a fresh pushed checkout with isolated Codex home: exact published SDK stdio diagnostic, authenticated localhost HTTP doctor, actual Codex skill discovery, rerun idempotence and preservation conflicts; distinguish this cached-dependency command proof from autonomous model installation, browser readiness and paid-provider execution.

- Add an instruction-only portable browser-automation skill for bounded decision-first Luna workflows, explicit browser choice, persistent IDs, context/privacy budgets, exact host approvals and independent rendered evidence; document current Codex/Claude/Hermes discovery without a mandatory-skill claim.
- Document optional authenticated Streamable HTTP deployment on the browser host, current/legacy protocol boundaries, compatible Codex/Claude and SDK configuration, per-identity ownership, bounds and a TLS reverse-proxy recipe; preserve default stdio and the OMP stdio bridge.
- Verify the HTTP/skill release with 81 unified tests, official SDK modern/legacy real-browser HTTP and plain localhost proxy fixtures, reference skill validation and installed Codex discovery; verify sdist skill/wheel assets and actual clean installed-wheel HTTP startup/tool listing/doctor. Record scoped fixture repairs and remaining live-model, personal-profile, installed-wheel browser-launch and nginx/TLS limits honestly.

- Establish an async Python package, CLI/MCP entrypoints, reproducible uv dependencies, and guarded-browser project contracts.
- Preserve native-profile consent, owned-tab lifecycle, independent completion verification, and feature-scoped Git ownership as durable working rules.
- Add native Chrome opt-in discovery and explicit isolated launch, revision-bound DOM/screenshot observations, and real-input target guards.
- Add provider-specific OpenRouter alpha/OpenAI multimodal Luna Decisions, structured field text, and independently verified bounded agent loops.
- Add persistent JSONL/MCP control and OMP public-tool integration, with host-controlled file paths and exact-action approval records.
- Document practical client setup, privacy boundaries, and dated research; make no unsupported comparison or live-provider verification claim.
- Verify the 0.1.0 source distribution/wheel includes `snapshot.js`, and execute CLI doctor from both the checkout and a clean installed-wheel environment without provider keys or native consent; record exact unified verification results and remaining live-provider/personal-profile boundaries in the evidence documentation.
