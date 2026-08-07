---
target: src/routes/Home.tsx
total_score: 17
max_score: 24
na_heuristics: 5,7,9,10
p0_count: 1
p1_count: 1
timestamp: 2026-08-07T22-43-36Z
slug: src-routes-home-tsx
---

Method: dual-agent (A: design review · B: detector + evidence)

### Design Health Score

| #         | Heuristic                       | Score     | Key Issue                                                                                                             |
| --------- | ------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------- |
| 1         | Visibility of System Status     | 3         | Static page, "draft-honest" motif works; nothing signals what happens after clicking CTA                              |
| 2         | Match System / Real World       | 3         | Plain language, no jargon — solid                                                                                     |
| 3         | User Control and Freedom        | 2         | Exactly one interactive element on the whole page; no lower-commitment path for a hesitant visitor                    |
| 4         | Consistency and Standards       | 3         | Drops the "eyebrow" label pattern used on `/campaigns/new`; card labels are `<p>` where the sibling route uses `<h3>` |
| 5         | Error Prevention                | n/a       | No user input exists on this page                                                                                     |
| 6         | Recognition Rather Than Recall  | 3         | Nothing to remember — solid                                                                                           |
| 7         | Flexibility and Efficiency      | n/a       | Single-path persuade-mode landing page                                                                                |
| 8         | Aesthetic and Minimalist Design | 3         | Restrained CTA is well-executed; ambient glow blobs drift off-content on wide viewports                               |
| 9         | Error Recovery                  | n/a       | No error states possible                                                                                              |
| 10        | Help and Documentation          | n/a       | No help affordance anywhere; reasonable for MVP                                                                       |
| **Total** |                                 | **17/24** | **Acceptable–Good (71%)**                                                                                             |

### Design Specificity Verdict

**LLM assessment:** Partially specific, uneven between copy and visuals. The subtitle is genuinely product-grounded — it names real pipeline stages and the approval gate. The visuals undercut it: the teaser shows only 2 of the product's 6 generated outputs (Strategy, Storyboard), and neither is "copy" or "video" — the two things the subtitle explicitly promises. The H1 is just the product's proper name restated in the heaviest type on the page, carrying zero value proposition. The human-approval gate — PRODUCT.md's stated core differentiator — has no visual representation at all, living only in a subordinate clause. Net: the copy is specific, the layout is a well-skinned but generic "headline + CTA + two floating cards" template.

**Deterministic scan:** `detect.mjs` returned clean (exit 0, `[]`) against `Home.tsx` itself — it's a thin JSX shell with no style literals. A supplementary scan of the paired `Home.module.css` (since that's where all visual decisions actually live) found 3 advisory `design-system-font-size` findings: line 66 (`.subtitle`, 1.1875rem — off-ramp), line 142 (`.teaserLabel`, 0.9375rem — off-ramp), line 208 (`.title` mobile override, 2.25rem — likely a false positive/documentation gap, since DESIGN.md's ramp doesn't specify responsive Display scaling). No detector/LLM disagreement — these are independent findings in different layers.

**Visual overlays:** Not available — no browser automation tool is exposed in this session, so the live-injection/overlay step was skipped per protocol rather than faked. Rendered-viewport behavior (layout at real widths, glow positioning, card rotation/clipping) was not visually confirmed.

### Overall Impression

A clean, well-crafted shell let down by weak content decisions. The CTA and card-shell mechanics are executed to spec; the page just doesn't argue the product's actual differentiator (coherence + human approval) to a skeptical first-time visitor.

### What's Working

1. **Cross-route visual coherence** — reusing `FloatingScrap` and the skeleton-bar/shot-grid vocabulary from `AIOutputPreview` ties Home into the same design language as `/campaigns/new`.
2. **CTA execution** — `.cta` implements the DESIGN.md button-primary spec precisely: gradient, `--shadow-hero`, sheen overlay, hover lift + glow, icon nudge, focus outline. The one loud element, done correctly.
3. **Copy quality** — the subtitle is concrete and specific ("plans the strategy, writes the copy, and cuts the video") rather than generic AI-marketing filler.

### Priority Issues

**[P0] Core differentiator (multi-asset coherence + approval gate) is nearly invisible visually**
Why it matters: this is the exact trust argument a wary small-business visitor needs before handing an AI their brand's video output — burying it undercuts the page's whole reason for existing.
Fix: represent more of the 6-stage scope (even compressed) and give the approval promise its own visual moment.
Suggested command: `/impeccable clarify`

**[P1] H1 is the product's proper name, not a headline; no eyebrow label**
Why it matters: the single heaviest text block on the page communicates zero benefit; inconsistent with `/campaigns/new`'s eyebrow+H1 pattern.
Suggested command: `/impeccable typeset`

**[P2] Ambient glow blobs mispositioned on wide viewports**
`.page::before/::after` anchor to the full-bleed `.page`, not the centered `max-width:1200px` `.workspace` — on viewports meaningfully wider than ~1200px the glows drift into empty margin.
Suggested command: `/impeccable polish`

**[P3] Missing intermediate responsive step for the hero title (641-960px band)**
`.title` holds full 3.25rem through the entire single-column range down to 640px, then jumps straight to 2.25rem — small laptops/tablets get an untuned, cramped hero.
Suggested command: `/impeccable layout`

**[P3] Teaser cards semantically thinner than their sibling on `/campaigns/new`**
`<p>` used for card labels where `AIOutputPreview` uses `<h3>`; no connective copy telling AT users these are output previews.
Suggested command: `/impeccable harden`

### Persona Red Flags

**Jordan (First-Timer):** H1 tells Jordan the product's name, not what it does differently from stitched-together point tools. The approval-gate reassurance — the biggest trust lever — is never visualized. Teaser cards undersell the actual 6-asset scope. No lower-commitment path exists before the only action is "Create campaign."

**Riley (Stress Tester):** Ambient glows detach from content on wide viewports (P2). No intermediate title breakpoint between 640-960px (P3). No `Home.test.tsx` exists anywhere in the routes folder — the site's literal front door has zero regression coverage, unlike `CreateCampaignPage.test.tsx`/`CampaignDetailPage.test.tsx`.

### Minor Observations

- `index.html`'s `<title>` is also just "AI Marketing Campaign Agent" — a related surface worth revisiting separately.
- Unused-space imbalance: copy column has more empty space below the subtitle than the teaser column, at desktop widths — not a defect.
- `FloatingScrap`'s hover state correctly gates on `@media (hover: hover) and (pointer: fine)`, excluding touch devices — done right.
- `SparkleIcon`, `MegaphoneIcon`, `BuildingIcon`, `UsersIcon` exist in `icons.tsx` unused in currently-read files — possibly intended for a fuller Home composition.

### Questions to Consider

1. If the approval gate is the product's biggest trust argument, why does the landing page treat it as a footnote?
2. What would the page look like if the teaser showed the _full_ six-stage strip instead of an arbitrary 2-of-6 sample?
3. Is "Create campaign" really the lowest-friction first action for a skeptical visitor, or would an example-output preview convert better first?
