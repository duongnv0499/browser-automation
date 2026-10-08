# Architecture

Python 3.11+ asynchronous code lives in `src/browser_automation`. Importing the package does not start a browser or construct a provider. `uv.lock` records the installation resolution. On 2026-10-08, `uv build` successfully produced the 0.1.0 source distribution and wheel; archive inspection confirmed `browser_automation/snapshot.js` is packaged. The installed wheel's CLI doctor also ran in a clean environment outside the checkout, without provider keys, native consent, or browser-library overrides. This checks packaging/import/CLI execution, not browser launch or live inference.

## Boundaries

- **BrowserSession (`browser.py`, `snapshot.js`)** owns Playwright transport, tab bookkeeping, snapshots, DOM references, stale/occlusion/operation guards, native profile discovery and isolated launch. Native mode reads opted-in debugging state with consent and never changes Chrome settings or silently changes profile. Closing an attached session disconnects and preserves user tabs; only session-owned tabs can be explicitly closed.
- **DecisionProvider (`providers.py`)** translates one trusted observation into provider-specific Decisions schemas. OpenRouter alpha and OpenAI use different question/image structures. One typed choice selects a compatible operation/target pair; freeform field text uses a separate call. Response validation rejects unknown actions/targets and refusals stop. See `providers.md` for shipped action-window and native-select behavior.
- **BrowserAgent (`agent.py`)** drives bounded observe/choose/act/reobserve cycles, approval callbacks and independent completion verification. DONE is a proposal, not success. History and stale retries are bounded. Cancellation stops the loop and propagates; the agent does not close caller-owned browser/provider objects. The caller or service manages their lifecycle and attached-session disconnect semantics.
- **Service and CLI/MCP (`service.py`, `cli.py`, `mcp.py`)** expose persistent browser sessions as an external tool surface. Agent imports are lazy to keep direct observation/control usable without constructing model clients. JSONL and MCP protocol stdout must contain only wire data.
- **OMP integration (`integrations/`)** uses an installed public tool-registration interface and a persistent child worker where available. No claim of a built-in Codex extension protocol bridge is made.

## Data and safety

Observations contain an opaque revision `id`, `tab_id`, URL/title, bounded page text, indexed elements, truncation flag and timestamp, with optional base64 PNG screenshot. A continuation reads text from the same cached revision, not a different page state. Elements expose role, name, value, supported operations, bounds and frame identity. Model data never becomes selectors or executable JavaScript.

Actions include observation revision, operation, compatible target and operation-specific values. Execution revalidates revision/tab/target/coverage and uses real browser input. Visual point refinement is constrained to an observed target's bounds, not unrestricted model coordinates.

Uploads/downloads require explicit approval and a host-controlled allowed directory. Approval must bind the exact operation, revision, target and filenames/destination; a model cannot approve its own policy or arbitrary directory. Provider inference sends selected page text/screenshots off-host; users must review sensitive content before enabling it. Remote debugging grants powerful profile access and must not be publicly exposed.

External CLI/MCP/OMP tool transports accept HTTP(S) pages and `about:blank`, not arbitrary local-file, data, JavaScript, or browser-internal URLs. Service checks navigation arguments and existing tab URLs before observation/input/inference, so a preexisting file tab does not authorize reading local secrets. This transport boundary is distinct from the trusted Python browser API and does not replace review of HTTP sites or provider data sharing.

## Completion and evidence

The agent separately verifies its outcome against a fresh observation. The final authorized 2026-10-08 suite passed 67 tests in 57.77s, combining offline transport/guard checks, real Chromium rendered-DOM fixtures, CLI/MCP subprocesses and installed OMP tool execution. Main reports focused source security rereview completed with zero unresolved high/medium findings; adversarial Chromium context tests also passed. This is not a blanket security guarantee. Native personal-profile attachment, headed desktop behavior, installed-wheel browser launch and live model requests remain unverified; controlled attached-browser fixtures and clean-wheel doctor do not establish those distinct acceptance categories. See `research.md` and `../AGENTS.md`.
