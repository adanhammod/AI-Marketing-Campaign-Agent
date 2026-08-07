---
target: Campaign Detail (CampaignDetailPage.tsx)
total_score: 17
max_score: 28
na_heuristics: 5,7,10
p0_count: 0
p1_count: 2
timestamp: 2026-08-07T21-55-00Z
slug: src-routes-campaigndetailpage-tsx
---

Method: dual-agent (A: design review · B: detector + browser evidence)

## Design Health Score

| #     | Heuristic                       | Score | Key Issue                                                                                                                                                                                     |
| ----- | ------------------------------- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | Visibility of System Status     | 2     | States are badged clearly, but there is no mechanism to learn about future changes -- no polling, no timestamp, no manual refresh, on a page whose whole job is monitoring an async pipeline. |
| 2     | Match System / Real World       | 3     | Plain labels throughout; one lapse -- "4 positioning points" and per-platform character counts are unexplained metadata jargon.                                                               |
| 3     | User Control and Freedom        | 2     | Only exit is "Back to home." No approve/request-revision control exists despite the page subtitle promising one.                                                                              |
| 4     | Consistency and Standards       | 3     | Consistent card pattern and semantics; storyboard and video share the identical emerald tone, and the campaign-id h2 renders smaller than card h3 titles.                                     |
| 5     | Error Prevention                | n/a   | Read-only monitoring page, no inputs/destructive actions to evaluate.                                                                                                                         |
| 6     | Recognition Rather Than Recall  | 4     | Everything visible, no icon-only nav, metadata surfaced directly.                                                                                                                             |
| 7     | Flexibility and Efficiency      | n/a   | Passive status page, no repeatable actions to accelerate.                                                                                                                                     |
| 8     | Aesthetic and Minimalist Design | 3     | Restrained and tone-tinted per DESIGN.md; docked for a grid gap and a metadata panel overlapping the image it describes.                                                                      |
| 9     | Error Recovery                  | 0     | AssetStatus is pending/generating/ready only -- PRODUCT.md documents backend FAILED/CANCELLED states, but the frontend has zero visual representation for a failed asset.                     |
| 10    | Help and Documentation          | n/a   | Simple glance page; the one jargon term is captured under #2 instead.                                                                                                                         |
| Total |                                 | 17/28 | Acceptable (61%)                                                                                                                                                                              |

## Design Specificity Verdict

Genuinely product-specific, not a reskinned template: each card renders the literal draft-shape of its own content (skeleton bars, dashed shot-grid, flat waveform) -- a direct, correctly-executed implementation of DESIGN.md own "Draft Room" spec, deliberately avoiding the generic spinner/icon-tile pattern its own Do not list forbids. Caveat: visual specificity is strong, interaction specificity is thin -- it is currently an inert grid of six non-interactive divs.

Deterministic scan (Assessment B): detect.mjs --json on CampaignDetailPage.tsx + AssetStatusCard.tsx -> exit 0, zero findings. Verified not an artifact of suppressed rules (no config/ignore files, no inline disables). Browser evidence unavailable (no headless browser in this environment) -- not attempted, no overlay claimed.

## What is Working

1. Draft-honest content shapes -- literal, undressed shape per content type, matching DESIGN.md north star exactly.
2. Correct, restrained a11y defaults -- decorative shapes aria-hidden, real ready-state images/metadata are not; role="list"/listitem used correctly on a div grid.
3. Progressive reveal done right -- the glass panel and real images only appear on genuine ready state, not always-on chrome.

## Priority Issues

- [P1] Approval promise with no mechanism -- subtitle says "Nothing is final until you approve it," but no approve/request-revision control exists anywhere in the app. Contradicts Product Principle #2. Suggested command: /impeccable shape
- [P1] No failed-generation state -- AssetStatus has no failed; once wired to live data, a failed asset will look identical to still pending forever. Suggested command: /impeccable harden
- [P2] Grid packing leaves an orphaned gap -- voiceover (span 1) sits alone before video (span 2) cannot backfill the dense grid; unintended-looking blank cell. Suggested command: /impeccable layout
- [P2] Borderline-failing contrast -- color-ink-muted (#64748b) on the page background computes to ~4.35:1, under the 4.5:1 AA minimum -- affects most secondary text on the page. Suggested command: /impeccable harden
- [P3] Heading hierarchy inverted -- the h2 campaign-id renders smaller/quieter than the h3 card titles inside it. Suggested command: /impeccable typeset

## Persona Red Flags

- Alex (power user): no auto-refresh/manual check, no ETA, cannot click into a bigger view of the images.
- Sam (accessibility): heading inversion breaks heading-nav; contrast shortfall noted above.
- Jordan (first-timer): unexplained metadata jargon; no ETA copy; will hunt for the "Approve" button the subtitle promises and not find it.

## Minor Observations

- Dead-code media query in AssetStatusCard.module.css (.wide collapse at 560px is already handled by the parent grid at 640px).
- storyboard/video tone collision (only repeat among six cards).
- The metadata reveal panel visually overlaps the real photo thumbnails it is describing rather than sitting beside them.

## Questions to Consider

1. What happens the moment every asset reads "Ready" -- where is the actual approve action?
2. Has anyone designed what this page looks like the first time a real asset fails?
3. Is a fully static page (no polling, no timestamp) really honoring "no fake progress," or does honest silence need an honest "last checked" instead?

## Resolution

User chose (2026-08-08): fold the P1 "no failed-asset state" finding into the next round (real pipeline stepper request) rather than address standalone. Remaining P1/P2/P3 items left as backlog.
