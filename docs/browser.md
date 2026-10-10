# Browser engine

The engine uses asynchronous Playwright Chromium for actual pointer/keyboard input, tab lifecycle, screenshots and CDP attachment. Observation, document/node identity, semantic freshness and hit tests run in **private CDP isolated worlds**, not in the site's JavaScript realm. Native CDP frame IDs and frame-owner backend node IDs connect main, same-origin and out-of-process cross-origin frames; URLs are not used as frame identities.

## Isolated browser

Install the browser binary once:

```sh
uv run playwright install chromium
```

```python
from browser_automation.browser import BrowserSession

async with await BrowserSession.launch(headless=False) as browser:
    tab = await browser.new_tab("https://example.com")
    observation = await browser.observe(tab["id"], screenshot=True)
```

`headless=False` requires a working desktop display. `headless=True` performs real Chromium rendering/input without a visible desktop window. These are separate capability claims: a headless screenshot is not proof that a desktop window was displayed. `executable_path` optionally selects a compatible local Chromium executable. Isolated contexts have independent cookies and accept downloads; closing them closes their owned browser.

## Existing logged-in Chrome: explicit CDP

```python
browser = await BrowserSession.connect("http://127.0.0.1:9222")
# A browser WebSocket URL is also accepted.
try:
    tabs = await browser.tabs()
finally:
    await browser.close()
```

An explicit CDP endpoint is a privileged connection. Do not expose it to untrusted networks. This is not arbitrary OS/browser-window automation and does not attach to Chrome without Chrome's permission/configuration.

## Existing default Chrome: consent and native opt-in

For Chrome versions offering the feature:

1. Open Chrome yourself with your normal profile; no profile restart is required by this engine.
2. Open `chrome://inspect/#remote-debugging`.
3. Enable **Allow remote debugging for this browser instance** yourself.
4. Authorize this engine with `consent=True`, and accept Chrome's per-connection **Allow** dialog if presented.

```python
browser = await BrowserSession.connect_default(consent=True)
# To avoid ambiguity with multiple Chrome installations:
browser = await BrowserSession.connect_default(
    profile_dir="/home/you/.config/google-chrome", consent=True
)
```

Discovery reads the profile's `Local State` opt-in and `DevToolsActivePort`; it probes the loopback `/json/version` endpoint and uses the Chrome-written browser WebSocket path when modern default-profile Chrome returns HTTP 404. HTTP 403 reports an explicit permission error. The engine never changes `Local State`, enables permission, clicks Chrome's approval dialog, restarts the profile, or substitutes an isolated browser on failure. A stale or absent port/opt-in produces an actionable failure. Default discovery checks normal Chrome paths for Linux, macOS and Windows; custom profiles require `profile_dir`.

This matches the current browser-harness documented Chrome opt-in/discovery mechanism, implemented with standard public CDP rather than cloning its daemon protocol. It is not a promise that every Chrome version supports this opt-in. Browser installation, remote debugging policy, enterprise restrictions and per-connection permission remain outside this engine's authority.

Sources: [browser-harness install instructions](https://github.com/browser-use/browser-harness/blob/main/install.md), [current daemon discovery](https://github.com/browser-use/browser-harness/blob/main/src/browser_harness/daemon.py), [Playwright CDP attachment](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp), [CDP isolated world](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-createIsolatedWorld).

### Ownership on disconnect

Attached-session `close()` disconnects the automation transport, **not the user's browser**. It also leaves owned tabs intact unless `close_tab()` was explicitly called. Existing user tabs cannot be closed with `close_tab()`. Popups from owned tabs are owned; popups originating in preexisting user tabs are conservatively not claimed for closure. `tabs()` discovers newly opened popups without switching focus or renaming titles. An isolated session closes its browser on exit. Browser keyboard shortcuts intended to operate Chrome's toolbar/window are not an OS-automation feature.

## Bounded navigation

`new_tab(url='about:blank', wait_until='domcontentloaded', timeout_ms=15000)` returns `id`, `url`, `title`, `navigation_status` (`complete` or `timeout`) and `wait_until`. Supported wait milestones are `commit`, `domcontentloaded` and `load`; timeouts are bounded to 1–120000 ms. `networkidle` is not exposed as a supported task-readiness milestone. A navigation timeout preserves the session-owned tab and returns a diagnostic, so the caller can inspect a partially rendered page and explicitly close it. Completion of a navigation milestone is not proof that the task or every resource finished. Invalid/protected navigation and hard navigation failures still fail safely; there is no automatic retry or CAPTCHA bypass.

`navigate(tab_id, url, wait_until='domcontentloaded', timeout_ms=15000)` moves an existing **session-owned** tab (created by `new_tab`, or a popup opened by an owned tab) and returns `{tab: {id, url, title}, navigation_status, wait_until}` with the same URL rules, milestones, bounds and timeout diagnostic as `new_tab`. A preexisting user tab in an attached browser is never navigated away: `NotOwnedTabError` (`code: "not_owned_tab"`, `recommended_next_action: "new_tab"`) is raised before any input; open a new owned tab instead. The tab's observations are invalidated before navigating, so older revisions fail as stale. A timeout retains the tab (`navigation_status: "timeout"` plus `navigation_timeout` diagnostic); a hard failure raises `NavigationFailedError` (`navigation_failed`) and also retains the owned tab, which may now show a protected error document that must not be observed. An owned tab currently on such a protected document may be navigated away; nothing from that document is read.

### Settling after input

Every `act` result carries `navigation: {started, status, url}`. Listeners for public Playwright page events are attached **before** input is dispatched: a main-frame navigation request (`request.is_navigation_request()` for `page.main_frame`) marks a cross-document navigation as started; `framenavigated` for the main frame marks a commit. After input the engine waits up to `settle_ms` (integer 0–10000; default 1000 for `click`, `press` and `drag`, 0 otherwise) for a navigation to start, and stops waiting as soon as one does.

- No main-frame navigation in the window: `{started: false, status: "none"}` after at most `settle_ms`; the navigation timeout is never spent.
- Same-document navigation (fragment link, `history.pushState`): the commit ends the window early and reports `status: "complete"` with the new URL; there is no new document to load.
- Cross-document navigation: wait for the commit, then `wait_for_load_state("domcontentloaded")`, the whole wait bounded by the action's `timeout_ms` (1–120000, default 15000). Result `complete` or `timeout` (with a `navigation_timeout` diagnostic; the tab is retained).
- A started request that ends without committing (HTTP 204, download, aborted) reports `status: "none"` with `reason: "not_committed"`; a page closed during settle reports `reason: "page_closed"`.

`reload` keeps its own `navigation_status`/`wait_until` and is never waited for twice. Iframe navigations are not tracked. `status: "complete"` only means the main frame reached `domcontentloaded`; it is a settle signal, never evidence that the action or task succeeded. Navigations started later than the window (for example after a slow API call) are not observed; observe again and verify the rendered outcome. Primary references accessed 2026-10-10: [Playwright Python Page API](https://playwright.dev/python/docs/api/class-page) (`goto`, `wait_for_load_state`, `framenavigated`) and the installed Playwright 1.63.0 client/server source, which clears load states before emitting a new-document navigation event.

## Observation and safe actions

`observe(tab_id, screenshot=False, max_text=12000)` returns an opaque revision, URL/title, visible text, actionable elements, explicit truncation metadata and timestamp. Each frame's DOM collection is synchronous/atomic; multiple frames and a screenshot are separate browser calls, not a globally atomic browser freeze. Screenshot observations undergo semantic revalidation before return. IDs are stable for the same node/document across observations, not after replacement/navigation. Frames and open shadow roots are traversed; viewport clipping includes scrolling ancestors and parent iframe boundaries. Partially visible text nodes are returned as complete text nodes, so text need not correspond one-to-one with pixels.

Each exported target is hit-tested once at the act guard's default input point, using the same composed-tree descent through open shadow roots. That point (shared code with the guard) is the center of the element's largest visible box fragment within its clipped visible box, so an inline link wrapped across lines is hit on its own text rather than at the center of its bounding box, which can be unrelated paragraph text. A target visible only as a sub-pixel sliver at the viewport edge cannot be hit-tested and is also reported as covered; scroll it into view. When non-descendant content is on top there, the element is still listed (with its name, so the agent can see it) but carries `covered: true` and an empty `operations` list, because every targeted operation would be rejected by the guard. Operations are part of the semantic digest, so an overlay that appears or disappears after an observation makes that revision stale; observe again. The flag only predicts the guard: it reflects occlusion inside the element's own document (parent-document overlays above an iframe are still caught only at input time), and the pre-input guard remains authoritative, for example for an explicit `x`/`y` point inside a partly covered target. `pointer-events: none` overlays do not count as covering, matching real clicks.

Bounds use top-level viewport CSS pixels. Frame metadata includes the document token and additive viewport offset. Rotated/scaled/transformed/zoomed iframe ancestors are omitted from DOM-coordinate control and reported in `limitations`; screenshot-bound visual targeting can still operate their rendered pixels. Offscreen/hidden/clipped-away frames do not advertise their controls. Scroll to reveal content and observe again.

`observe(..., text_scope='document')` returns readable text of each observed document beyond the viewport: rendered text nodes with a nonempty box, not `visibility: hidden` and without a fully transparent ancestor, regardless of viewport or overflow clipping (so collapsed carousels or clipped panels can contribute text). Elements, targets and `rendered_text_nodes` stay viewport-bound, and the semantic digest still uses only viewport text, so offscreen text never makes a revision stale. Document text has its own budget within the 1,000,000-character collector limit; frames that are entirely outside the viewport are still skipped and reported in `limitations`. Exported elements may also carry `expanded` (`aria-expanded`), `selected` (`aria-selected="true"`) and `disabled` (`aria-disabled="true"`); these are informational and not part of the digest.

Text is never silently cut: `truncated`, `text_length` and `next_offset` describe the cut. Retrieve the exact cached revision's remaining text with:

```python
part = await browser.text_continuation(
    tab_id, observation["id"], offset=observation["next_offset"], max_text=12000
)
```

A call can request up to 1,000,000 characters; longer observations remain retrievable in successive chunks. Continuations return cached text, not newly scraped text. The last eight observations are retained globally; expired or acted-on revisions require reobservation. No text-only claim covers canvas imagery.

`coverage` describes DOM collection independently of text truncation: `status` is `complete`, `partial`, or `unknown`, with reasons and DOM-element counts. A non-truncated empty DOM is not proof that a rendered page has no controls. Custom elements, open shadow roots and `display:contents` must not disappear merely because an ancestor has no box; clipping still applies to the actual rendered descendants. Closed roots/canvas and skipped frames remain limitations. `page_state` supplies bounded DOM-derived state/evidence, visible alerts and recovery recommendations; it is heuristic, not OCR or calibrated certainty. For optional visual interpretation, see [providers](providers.md#page-state-and-opt-in-visual-interpretation).

Collection and pre-input guards honor HTML root/body overflow propagation to the viewport rather than treating a zero-height body as a clipping rectangle when its overflow is propagated. This is a generic rendered-layout rule, not a YouTube special case. Actual local clipping, hidden descendants and box-generating zero-size overflow containers remain enforced. Primary reference accessed 2026-10-09: [CSS Overflow3 viewport propagation](https://drafts.csswg.org/css-overflow-3/#overflow-propagation) (dated editor's draft).

Actions are dictionaries containing `observation_id`, `operation`, and, when needed, `target`. Supported operations: `click`, `fill`, `select`, `scroll`, `press`, `hover`, `drag`, `wait`, `back`, `forward`, `reload`. There are no external selector or JavaScript execution APIs. For example:

```python
await browser.act(tab_id, {
    "observation_id": observation["id"],
    "operation": "fill", "target": textbox["id"], "text": "hello"
})
```

The engine validates supported payloads before invalidating the revision or dispatching input. It rejects wrong-tab revisions, changed documents/frame topology, changed visible text/control semantics and changed form values (including non-visible fields), replaced/detached targets and covered/inert/disabled targets. Private semantic fingerprints never expose password values. Fresh clipping-aware geometry and composed-tree hit tests are checked immediately before dispatch, including parent overlays above cross-origin frames. Mouse/keyboard input is native browser input; fill clicks/focus-checks the field and sends select-all/text input. `press` accepts validated key names/chords; targetless keys act on the currently focused browser content. `scroll` accepts `delta_x`/`delta_y`, and targeting a scroll container moves the pointer there before the native wheel event. `drag` requires an observed `to_target` and optionally `to_x`/`to_y`. `wait` is bounded to ten seconds. History actions require a valid observation. Optional `settle_ms` and `timeout_ms` control [settling after input](#settling-after-input).

Native select and file-input operations use trusted isolated-world HTML form APIs and normal input/change events, equivalent to browser automation form setters; they are not simulated OS file-picker or mouse-menu interactions. Select values must correspond to observed enabled options. Upload files become browser `File` objects with their actual approved bytes.

`check_observation(tab_id, observation_id, targets=())` repeats the pre-input revalidation (`_snapshot` plus semantic/topology `_validate`, and the target-pixel comparison for screenshot-bound visual targets) without sending input; transports use it before pausing for approval. An action invalidates its tab's observations even if dispatch later fails. `StaleObservationError.code == "stale_observation"` only denotes failures before input, so reobserving/retrying is safe. A failed operation after a click/key/setter may have changed the page and must not be blindly retried. Browser input and asynchronous page changes are separate protocol messages: **no engine can promise an atomic check-plus-input against continuously mutating or adversarial page layout**. The guards fail conservatively; agents must reobserve and separately verify actual outcomes.

### Canvas and screenshots

`screenshot=True` adds a viewport PNG as base64 plus 64 bounded `visual-region` targets in an 8×8 grid. Grid actions use cell centers by default. Supply precise `x`/`y` in CSS pixels inside the observed region to refine a pixel target; DOM canvas targets likewise accept an in-bounds point. Drag destinations support bound `to_x`/`to_y`. These points refer to the exact screenshot revision, never to model-generated JavaScript or unbounded coordinates.

Visual actions revalidate semantic context, geometry and occlusion, then compare exact decoded RGB pixels across the full observed target crop (and drag destination) from one coherent screenshot, rather than requiring whole-viewport PNG equality. Unrelated animation outside those regions need not invalidate input; changed target pixels still fail closed with `target_pixels_changed`, `changed_region` and `target_bounds` diagnostics. No pixel tolerance, automatic replay or guard bypass is implied. Each baseline/current PNG is bounded to 32 MiB and decoded images to 8,388,608 pixels; over-cap images are rejected. Continuously changing targets may remain unusable. This guard costs an additional screenshot for visual dispatch, not ordinary DOM actions. Browser check-plus-input cannot be globally atomic.

### Privacy and URL boundaries

Known sensitive fields (`password`, password autocomplete, credit-card autocomplete, one-time-code) have redacted values and are masked in screenshots. Normal text, page URLs, labels and screenshots can still contain personal data or secrets rendered outside those fields. Review provider disclosure consent; this is not a general-purpose PII detector or security sandbox for arbitrary webpages.

Navigation/control/observation permit HTTP(S) and `about:blank`; inherited blank/srcdoc subframes are allowed. Protected top-level documents (`file:`, `javascript:`, `chrome:`, extensions, `data:`/`blob:` and other unsupported schemes) remain denied without title/text/screenshot disclosure. Unsupported subframes are skipped with safe frame identity, scheme and reason metadata, not protected content or full URLs. Hidden data/blob frames do not block the parent DOM/screenshot. Visible or geometry-unknown protected frames cause screenshot withholding with an explicit diagnostic, while preserving permitted main-page DOM; no visual grid is returned and skipped-frame targets cannot be acted on. This is not a blanket data/blob evaluation allowlist. Protected links/forms are rejected before input; redirects/history are checked before observation disclosure. Use isolated browsers for untrusted websites and keep Chrome's sandbox enabled.

Input is also blocked when a permitted parent wrapper's pointer point or drag path enters a skipped protected frame, or an unbound key would operate a focused protected frame. A parent DOM target ID is not permission to enter that frame. The allowed parent remains controllable outside those excluded areas.

## Scoped network and application WebSocket diagnostics

Single-tab monitors retain `monitor_start(tab_id, kind='network'|'websocket', ...)`, list and stop behavior. Network capture additionally supports explicit multiple existing tabs and opt-in future tabs/popups; use the external `network_start_many`, `network_list_many` and `network_stop_many` tools. Start before the interaction: previously completed requests are not reconstructed and early popup traffic may precede attachment. Capture generation, per-tab cursors/drop/history and individual failures disclose missing evidence.

Network `network_detail` and `network_body` inspect actual selected queries, headers, request payloads and response bytes, not just metadata. Defaults retain sanitized lightweight event lists and scrub recognized sensitive detail/body values; authorized sensitive output requires caller `include_sensitive` plus host `BROWSER_NETWORK_SENSITIVE=1`. Bodies are retrieved on demand, chunked and bounded with explicit loss/unavailable metadata; uploads, cached and streaming resources are not promised complete bytes. `network_call`/`network_replay` use the selected browser context cookie jar with trusted safe-read dispatch or exact host approval. These API outcomes are separate from page Network events and do not render the DOM. See [the complete workflow, privacy and limits](network.md).

HTTP 503 is a response, not `requestfailed`. WebSocket events concern website sockets, not the CDP attachment socket; opcode/size/lifecycle are metadata by default. Text payload capture requires both explicit call opt-in and host `BROWSER_MONITOR_PAYLOADS=1`, stays bounded/redacted, and can still contain sensitive data. Redaction is not a comprehensive secrecy guarantee. Binary WebSocket data is not returned as payload; binary HTTP bodies can be returned in base64 chunks.

WebSocket `capture_scope: "main_page_cdp_target"` explicitly excludes worker and out-of-process child-target sockets; it is not complete browser-wide or complete-tab socket inventory. Chromium's CDP omitted actual protocol ping/pong control frames in the local fixture, so `control_frame_visibility: "not_guaranteed_by_cdp"` discloses that limit. Text messages named ping/pong are ordinary opcode-1 messages, never synthesized opcode9/10. Starting an already-active monitor with identical options is idempotent (`already_active`); changing options requires explicit stop then start. Cleanup errors are bounded safe diagnostics, not silently invented successful teardown. CDP reports whole messages rather than fragmented wire-frame boundaries.

## Explicit uploads and downloads

The engine-level `approved=True` arguments below are trusted host decisions. Transports derive them from the host approval mode (`BROWSER_APPROVAL_MODE`, see [integrations](integrations.md#host-approval-modes)): an exact token in `strict`/`standard`, standing host approval in `autonomous`, with the directory scope always enforced.

File transfers are **host-policy operations**, not actions through which a model grants itself filesystem permission. Bind `allowed_directory` to a trusted host/user configuration, and set `approved=True` only after approval of the exact operation/target/revision/paths. Transport services bind approval tokens separately.

```python
await browser.upload(tab_id, observation_id, file_input_id,
    ["/approved/input/report.csv"], allowed_directory="/approved/input",
    approved=True)

await browser.download(tab_id, click_action, "/approved/output/report.pdf",
    allowed_directory="/approved/output", approved=True)
```

Secure transfer confinement is currently POSIX-only. Each root/ancestor is opened with directory descriptors and `O_NOFOLLOW`; roots and path components must not be symlinks. Upload reads regular-file bytes from retained descriptors before supplying browser file payloads: maximum 32 files, 64 MiB per file and 128 MiB total. Path replacement cannot make Playwright reopen a different outside file. Downloads are captured into a private staging directory, then streamed to a newly/exclusively created output descriptor relative to the pinned destination ancestor. Existing destinations are never overwritten and a replaced pathname cannot redirect the open output descriptor. In a directory-renaming race, writes remain anchored to the originally approved directory inode, not an attacker-supplied replacement. Failure during copying may leave a partial newly created file; cleanup never unlinks a possibly attacker-replaced pathname. Cancellation waits for an already-running descriptor copy to settle before releasing its directory/staging resources.

Windows/other platforms without these descriptor guarantees reject secure file transfer explicitly. Browser observation/control and explicit CDP are not restricted to POSIX. Native OS picker dialogs, directory upload, browser permission dialogs, closed shadow-root DOM inspection and desktop chrome are not implemented. Closed shadow/canvas content can be seen and pointed at through screenshots when stable, but semantic DOM targeting cannot see into closed roots. Downloads remain subject to browser/enterprise policies and provider/browser permissions are never bypassed. No CAPTCHA, bot-detection bypass, stealth or competitor-superiority claims are made.

## Acceptance fixtures

`tests/test_browser*.py` contain real Chromium cases for owned-tab navigation (rendered DOM and screenshot, stale revision, prohibited URLs, timeout retention, preexisting-tab refusal), act settle (link, fragment and `pushState` navigation, bounded non-navigating clicks, timeout), covered-target flags (no operations while covered, stale revision when the overlay is removed, guard rejection of an explicit covered point), service stale-before-approval and tab-cache invalidation, input/select, keyboard/hover/container scrolling/drag/history, popup discovery, cross-origin frames and open shadow roots, stale/context/covered/wrong-document rejection, screenshot-bound canvas points, continuation, approved confined upload/download and attached disconnect retaining the external browser/preexisting tab. Security cases cover hostile page-realm registry/built-in replacement, prohibited local documents, duplicate native frame identities and symlink/directory replacement. Run these only in the project's authorized integration verification phase. Set `BROWSER_PROOF_DIR` to persist the PNG/rendered-DOM evidence and browser-only timing JSON. A throwaway external Chromium CDP test is not evidence of a live personal Chrome profile connection; real user opt-in and per-connection approval must be exercised separately on that user's desktop.

`tests/test_network_details.py`, `test_network_requests.py`, `test_network_multitab.py` and `test_network_surface.py` additionally exercise actual local HTTP/Chromium queries/headers/JSON/form/binary bodies, chunking/privacy, no-resend body recovery, multi-tab/future-popup lifecycle, exact request plans, cookie-context edited replay, redirect stops and issued cancellation discovery. The authorized unified gate passed **196 tests**; clean installed-wheel public client workflows/cancellation passed **8 cases**. Parent independently viewed the installed current-HTTP QuietUI screenshot and server evidence. See [network acceptance](research.md#network-release-gate--2026-10-09); API response success is not a rendered UI-change claim or proof of a personal native account.
