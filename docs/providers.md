# Decisions providers and agent safety

## Confirmed wire formats (research 2026-10-08)

The default is **OpenRouter alpha Decisions**, model `openai/gpt-6-luna-decisions`, POST `https://openrouter.ai/api/alpha/decisions`. Questions and answers are maps keyed by question name; choice questions use a `criteria` map. Choice probabilities are a map and confidence/probabilities are optional in the alpha response schema. Missing verification probabilities are rejected rather than invented.

**Actual OpenRouter image support is confirmed**, not a chat fallback or JSON-base64 guess: `state` is a top-level array containing plain text strings and `{ "type": "image_url", "image_url": { "url": "data:image/png;base64,..." } }`. Images nested in objects, `input_image` parts, remote image URLs, and raw base64 strings are not this transport. The official multimodal guide lists Luna with text/images and at most 128 images; this client sends one PNG screenshot. Other documented image-capable models are allowlisted; unknown models require text-only mode, never silently omit supplied screenshots.

OpenAI uses POST `https://api.openai.com/v1/decisions`, model `gpt-6-luna`. Its `input` is text or a user-message array with `input_text` and `{ "type": "input_image", "image_url": "data:image/png;base64,..." }`. Questions/answers are arrays with `name`; choices are arrays with value/description; probabilities are arrays of value/probability. A refusal stops execution. Confidence is independent of selected probability; neither equality nor argmax is presumed.

Primary sources:

- [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions)
- [OpenRouter alpha API schema](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
- [OpenRouter multimodal Decisions image specification](https://openrouter.ai/docs/guides/community/multimodal-decisions.md)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs.md)

## Configuration

`DecisionProvider.from_env(provider='openrouter', model=None, transport=None, vision=None)` uses `OPENROUTER_API_KEY` or `OPENAI_API_KEY`. Never put keys in a URL. Error messages exclude request bodies, provider response bodies, and keys. HTTPS is required except loopback local test endpoints. HTTP redirects and automatic request retries are disabled.

| Environment variable | Purpose/default |
| --- | --- |
| `BROWSER_AGENT_MODEL` | Override Decisions model (default Luna for selected provider) |
| `BROWSER_AGENT_TRANSPORT` | `decisions` only; no automatic chat fallback |
| `BROWSER_AGENT_DECISIONS_ENDPOINT` | Full Decisions URL, not a base URL |
| `BROWSER_AGENT_TIMEOUT` | Request timeout seconds, default 30 |
| `BROWSER_AGENT_VISION` | `true`/`false`; default on for documented image models |
| `BROWSER_AGENT_TEXT_PROVIDER` | Independent field-text provider; default selected provider |
| `BROWSER_AGENT_TEXT_MODEL` | Default `openai/gpt-4.1-mini` on OpenRouter, `gpt-4.1-mini` on OpenAI |
| `BROWSER_AGENT_TEXT_ENDPOINT` | Full chat completions URL for field text |

The text provider uses its own provider API-key environment variable. `capabilities` exposes model, transport, vision and text-model configuration without keys. Models and provider availability change; an API rejection is explicit and not replaced by a different model. Page text and screenshots are sent to the selected external service: obtain user consent for the attached profile and sensitive page content before use.

## Decision/control contract

Each step sends **one joint operation+observed-target/value choice head**. Compatible actions are paged into at most 48 choices (or a lower configured limit) with explicit `action_window` totals, offsets, omitted-action counts and `omitted_elements`. `next_actions`/`previous_actions` inspect further windows without browser input; every observed compatible target and enabled select option is reachable. Relevant descriptors and option labels accompany each window, while page text and screenshot remain evidence. Context has a 48,000-character budget; oversized user/page text is rejected explicitly, not silently lost. Drag blocks are counted without enumerating all source/destination products. This avoids dependent speculative questions and unbounded payloads.

Only fill invokes the small structured-text model. Its helper receives the PNG via Chat Completions `image_url` parts when present; it never silently drops visual labels. Select choices bind the literal observed enabled option value in the Decisions request, with no free-text request. Autonomous controls include target scroll, drag to observed targets (with approval), and canvas/screenshot-region point clicks. Hierarchical `refine_point` choices divide the observed region into 3×3 cells for up to six no-input levels, followed by a cell-center click bound to original target bounds. Precision is discrete (one third per level), not arbitrary free coordinate generation; very tiny/unrecognizable controls may still require user assistance. File operations remain explicit consent/path-scoped browser tools rather than model-generated paths.

Multiple-select choices toggle one observed enabled option and retain the current selected-value list; repeated bounded choices can form any reachable selection without generating an exponential powerset. Pixel-only changes count as progress through a stable screenshot hash. Targeted keyboard input uses browser-native focus without an implicit click; activating Enter/Return/Space keys still require consequential-action consent.

`next_text`/`previous_text` read cached text windows of the same observation revision, with offsets, captured text length, next offset and source-truncation metadata. No-input text paging counts toward the step bound. Before independent verification, the prefix and at most two most recently visited continuation windows are fetched from a **fresh** observation, not stale history; omitted evidence never proves a condition. Source beyond the browser's hard collection cap is unavailable in that snapshot and may require scrolling/reobserving or user assistance. Truncated text is not silently treated as complete.

Action-created popup IDs become host-observed `switch_tab` choices, with URLs/titles; the loop never invisibly jumps into arbitrary existing or unsolicited tabs. A model-selected switch observes that tab, preserves the original tab as an explicit return choice, and reports `active_tab` in the result. Approval binds to the actual active tab. Tabs remain under the BrowserSession ownership/consent rules.

`BrowserAgent.run(tab_id, goal)` is bounded by steps, history, repeated no-progress and pre-input stale observations. Results distinguish success, verification_failed, approval_required, no_progress, blocked, refused, provider_error, cancelled and step_limit. A DONE choice is **never** success by itself: a fresh observation plus a separate goal-verification request must meet the configured probability threshold (default 0.9). Probabilities are model judgments, not calibrated safety guarantees. The verifier may use the same model but does not reuse the choice answer.

Page DOM, screenshots, labels and history are untrusted evidence, never authority to change the user goal or grant approval. Conservative host-side approval gates cover consequential labels, unknown button/custom clicks, sensitive fields, drag/drop and Enter submission. Clearly labeled search, menu, expand/collapse and next/previous navigation buttons are allowed only without observed submit/post/risky semantics. This heuristic is not a universal semantic safety proof; users should supervise real accounts. The approval callback receives `{tab_id, observation_id, action, reason, binding}` and must return exactly `True`. Its action is copied and includes actual generated input. Consent binds to that exact action and revision. The browser validates stale/covered/wrong-tab state before input. Only guaranteed pre-input stale errors permit reobservation and a new decision; uncertain failures never replay an action. Cancellation stops future actions but cannot undo an input already sent.

On `approval_required`, the Python host may approve the returned exact action, call `session.act(tab_id, result['approval']['action'])`, then rerun the same goal to observe progress and independently verify. Do not regenerate a new action and transfer consent. If the original snapshot became stale, the host must obtain new observation/action consent. MCP/CLI service retains the pending exact action and offers `approved_act` with host-minted one-shot session/tab/revision-bound approval, expiry and the browser's pre-input revalidation; it then continues through a fresh goal run. There is no blanket model-generated consent.

No CAPTCHA, stealth, detection-bypass, or comparative superiority claims are made. Metrics separate measured browser time, provider latency, context/request sizes and token usage. Local deterministic HTTP/browser fixtures demonstrate transport/control correctness, **not live Luna intelligence or paid API latency**. Live provider end-to-end measurements require credentials and must be reported separately.
