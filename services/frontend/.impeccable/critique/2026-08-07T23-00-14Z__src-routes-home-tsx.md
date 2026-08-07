---
target: src/routes/Home.tsx
total_score: 17
max_score: 24
na_heuristics: 5,7,9,10
p0_count: 1
p1_count: 0
timestamp: 2026-08-07T23-00-14Z
slug: src-routes-home-tsx
---

Method: dual-agent (A: design review · B: detector + evidence) — post-change re-run

### Design Health Score

| #         | Heuristic                       | Score     | Key Issue                                                                      |
| --------- | ------------------------------- | --------- | ------------------------------------------------------------------------------ |
| 1         | Visibility of System Status     | 3         | Unchanged from baseline; not touched by this pass                              |
| 2         | Match System / Real World       | 3         | Unchanged; new H1 is plainer but more generic phrasing                         |
| 3         | User Control and Freedom        | 2         | Unchanged; still one interactive element, no lower-commitment path             |
| 4         | Consistency and Standards       | 3         | `<h3>` fix closes one gap with AssetStatusCard; eyebrow-label absence persists |
| 5         | Error Prevention                | n/a       | No user input exists on this page                                              |
| 6         | Recognition Rather Than Recall  | 3         | Unchanged — solid                                                              |
| 7         | Flexibility and Efficiency      | n/a       | Single-path persuade-mode landing page                                         |
| 8         | Aesthetic and Minimalist Design | 3         | Glow-blob positioning bug (baseline P2) verified fixed                         |
| 9         | Error Recovery                  | n/a       | No error states possible                                                       |
| 10        | Help and Documentation          | n/a       | No help affordance anywhere; reasonable for MVP                                |
| **Total** |                                 | **17/24** | **Acceptable–Good (71%), unchanged raw score — see note below**                |

Raw total is unchanged from the baseline run, but the composition shifted: real fixes landed (H1 copy, card label semantics, glow positioning) while a new small accessibility gap appeared in the same pass (missing "example" disclosure for screen-reader users on the new image strip) — since fixed in this same session before finalizing (see below). Net effect nets out near the same total but the page is not in the same state as before.

### Design Specificity Verdict

**LLM assessment:** The subtitle is unchanged and remains genuinely product-grounded. The teaser column now shows three cards (Strategy, Storyboard, Images) instead of two, but only "Strategy" overlaps with what the subtitle names (strategy/copy/video) — copy and video are still never shown, and Storyboard/Images are never named in the subtitle. With both sides now at exactly three items, the near-zero overlap reads more like a content mismatch than a deliberate abbreviated sample. The new H1 ("Grow your business with AI-generated campaigns") is a real improvement over restating the product name, but is generic AI-SaaS phrasing that still doesn't carry the actual differentiator (coherence + human approval), which remains buried in a subtitle clause.

**Deterministic scan:** Both `Home.tsx` (exit 0, clean) and `Home.module.css` (exit 2, 3 advisory findings) were re-scanned. All 3 findings are the same pre-existing `design-system-font-size` items from the baseline run, just line-shifted by the new code inserted above them (line 66→65, 142→148, 208→231) — no value or selector changed, and no genuinely new findings were introduced by the added card, the `.teaserLabelRow`/`.exampleBadge`/`.imageStrip`/`.imageThumb` CSS, or the moved `.workspace::before/::after` glow pseudo-elements.

**Visual overlays:** Not available — no browser automation tool exposed in this session; skipped per protocol, not faked.

### Overall Impression

Real, verifiable fixes landed (glow positioning, heading semantics, headline copy) and the new Images card is a genuine emotional upgrade — the first payoff moment on the page. The core structural gap (product differentiator + approval gate having no visual presence) persists and, if anything, is more conspicuous now that the teaser and subtitle both name exactly three things that barely overlap.

### What's Working

1. **Glow positioning fix verified correct** — moved from `.page::before/::after` to `.workspace::before/::after` with `z-index: -1`, now anchored to the centered 1200px content box instead of the full-bleed page.
2. **Card label semantics fixed** — all three teaser cards now use `<h3>`, matching `AssetStatusCard`'s convention.
3. **Images card is a well-executed reuse of an established pattern** — same coral tone mapping, same fixtures, same `imageThumb`/aspect-ratio treatment as `AssetStatusCard`'s ready state, and gives the page an actual visual payoff moment it didn't have before.

### Priority Issues

**[P0] Core differentiator still has no visual presence; copy/visual mismatch is now more conspicuous** (carried over, not addressed this pass — redesign-scale, out of bundled scope)
Suggested command: `/impeccable clarify`

**[P1 → fixed during this critique pass] New Images card initially lacked an accessible "example" disclosure**
The image strip was the only teaser shape without either `aria-hidden` or an accessible signal distinguishing it as a static example — screen-reader users would hit plausible-sounding alt text ("Product hero visual for the campaign") with no indication it's a fixture, risking a false-state impression the product's own "show real state honestly" principle is meant to prevent. **Fixed in this session**: alt text now reads "Example: product hero visual for a generated campaign" (and equivalent for the other two), so the disclosure reaches AT users, not just sighted ones.

**[P2] H1 fix is half-done — still no eyebrow label, and the new headline is generic** (carried over, redesign-scale, out of bundled scope)
Suggested command: `/impeccable typeset`

**[P3] Missing responsive title breakpoint (641–960px band)** — confirmed systemic, also present on `/campaigns/new`; not touched this pass.
Suggested command: `/impeccable layout`

**[P3] Abstraction register now inconsistent across the three teaser cards** — Strategy/Storyboard follow the "draft-honest" skeleton convention; the new Images card shows a polished real example instead. The "Example output" badge (and now the alt-text fix) keeps it honest, but the rhetorical register differs card-to-card.
Suggested command: `/impeccable clarify`

### Persona Red Flags

**Jordan (First-Timer):** The H1 fix genuinely helps — leads with a benefit now instead of a bare product name. Still never sees the approval gate visualized; may notice the subtitle-vs-teaser content mismatch on a quick scan.

**Sam (Accessibility-Dependent):** Was the persona most affected by this change set — the missing example-disclosure for screen readers has been fixed in this session (alt text now includes "Example:").

**Riley (Stress Tester):** Baseline P2 (glow drift on wide viewports) is resolved. No intermediate title breakpoint and no `Home.test.tsx` both persist unchanged.

### Minor Observations

- Card rotation values are `-1.5deg, 1.5deg, -1deg` — not a strict zig-zag (third card repeats the first's sign); likely imperceptible.
- `exampleBadge` is unstyled plain caption text (no pill/background) — a deliberate register choice since it labels provenance rather than status, distinct from `AssetStatusCard`'s filled-pill `statusBadge`.
- `index.html`'s `<title>` is still the generic product name — unchanged, separate surface.
- `SparkleIcon`, `MegaphoneIcon`, `BuildingIcon`, `UsersIcon` remain unused relative to `Home.tsx`.

### Questions to Consider

1. With the subtitle and teaser both naming exactly three things that barely overlap, should the subtitle be rewritten to match what's shown, or does the mismatch point at the wrong three assets being teased?
2. Is a labeled real "Example output" card the right honesty mechanism next to two deliberately-abstract draft cards, or does mixing registers in one three-card row undercut the system's own restraint?
3. Two review passes have now landed on this file without the approval gate gaining any visual presence — what would it take to make that a design priority rather than a recurring backlog item?
