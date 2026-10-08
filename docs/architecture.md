# Architecture

Python 3.11+ asynchronous code lives in `src/browser_automation`. Importing the package does not start a browser or construct a provider. `uv.lock` records the installation resolution; Hatch includes package files such as `snapshot.js` in the wheel.

## Boundaries

- **BrowserSession (`browser.py`, `snapshot.js`)** owns Playwright transport, tab bookkeeping, snapshots, DOM references, stale/occlusion/operation guards, native profile discovery and isolated launch. Native mode reads opted-in debugging state with consent and never changes Chrome settings or silently changes profile. Closing an attached session disconnects and preserves user tabs; only session-owned tabs can be explicitly closed.
- **DecisionProvider (`providers.py`)** translates one trusted observation into provider-specific Decisions schemas. OpenRouter alpha and OpenAI use different question/image structures. Operation and compatible-target heads share a request; freeform field text uses a separate call. Response validation rejects unknown actions/targets and refusals stop.
- **BrowserAgent (`agent.py`)** drives bounded observe/choose/act/reobserve cycles, approval callbacks and independent completion verification. DONE is a proposal, not success. History and stale retries are bounded, and cancellation releases only owned resources.
- **Service and CLI/MCP (`service.py`, `cli.py`, `mcp.py`)** expose persistent browser sessions as an external tool surface. Agent imports are lazy to keep direct observation/control usable without constructing model clients. JSONL and MCP protocol stdout must contain only wire data.
- **OMP integration (`integrations/`)** uses an installed public tool-registration interface and a persistent child worker where available. No claim of a built-in Codex extension protocol bridge is made.

## Data and safety

Observations contain an opaque revision `id`, `tab_id`, URL/title, bounded page text, indexed elements, truncation flag and timestamp, with optional base64 PNG screenshot. A continuation reads text from the same cached revision, not a different page state. Elements expose role, name, value, supported operations, bounds and frame identity. Model data never becomes selectors or executable JavaScript.

Actions include observation revision, operation, compatible target and operation-specific values. Execution revalidates revision/tab/target/coverage and uses real browser input. Visual point refinement is constrained to an observed target's bounds, not unrestricted model coordinates.

Uploads/downloads require explicit approval and a host-controlled allowed directory. Approval must bind the exact operation, revision, target and filenames/destination; a model cannot approve its own policy or arbitrary directory. Provider inference sends selected page text/screenshots off-host; users must review sensitive content before enabling it. Remote debugging grants powerful profile access and must not be publicly exposed.

## Completion and evidence

The agent separately verifies its outcome against a fresh observation. Tests cover offline transport/guard behavior, while real browser verification must assert rendered DOM outcomes and save screenshots. Native attachment and live model requests remain distinct acceptance evidence; lack of credentials or user Chrome must be disclosed rather than masked by an isolated/mock success. See `research.md` and `../AGENTS.md`.
