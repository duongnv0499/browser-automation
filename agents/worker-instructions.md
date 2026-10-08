# Worker handoff

Read `../AGENTS.md` and `workstreams.json` at startup and after compaction. Work only on assigned paths; coordinate shared contracts through the parent and never modify another worker's files without consent. Research unfamiliar external APIs against current primary documentation before implementing them. Keep a compact `agents/<worker>.json` record of contract decisions, implementation state, blockers, and requested verification.

Deliver usable implementations, updated feature documentation, and tests—not scaffolds or mock-backed runtime behavior. Mocks belong only in explicitly named offline tests. Native browser consent and owned-tab lifecycle are not optional. Model refusals and unapproved risky actions must not execute. Reject stale/covered/wrong-tab targets before browser input and verify outcomes separately from DONE.

Do not run checks during implementation. Once all assigned code is complete, report READY to the parent, with exact tests/smokes requested. After parent authorization, exercise real browser rendering, capture screenshots plus DOM outcomes, and fix your paths. Report unavailable live-provider credentials and unavailable native user Chrome explicitly; do not infer live success from offline transport tests.

For each stable feature milestone send Harness the exact paths to stage and a meaningful commit message. Harness serializes commit/push operations. Do not stage shared/unowned work, force push, or delete another worker's changes. Preserve the user's `doc.md`.
