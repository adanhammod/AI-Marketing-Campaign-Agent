---
name: AI Marketing Campaign Agent
description: An AI tool that turns one campaign brief into a full, human-approved marketing campaign.
colors:
  royal-blue: '#2563eb'
  royal-blue-deep: '#1d4ed8'
  working-indigo: '#4f46e5'
  working-indigo-soft: '#eef2ff'
  bright-cyan: '#06b6d4'
  studio-teal: '#14b8a6'
  studio-teal-soft: '#e3f8f5'
  confirming-emerald: '#10b981'
  confirming-emerald-soft: '#e7f9f2'
  warm-coral: '#fb7156'
  warm-coral-soft: '#fff1ed'
  signal-amber: '#f59e0b'
  signal-amber-soft: '#fef3e2'
  sky-blue: '#38bdf8'
  sky-blue-soft: '#eaf7fe'
  whisper-lavender: '#c4b5fd'
  danger: '#b3261e'
  danger-soft: '#fbeae9'
  paper-white: '#f7fafc'
  cool-mist: '#eef6fa'
  pure-white: '#ffffff'
  near-black-ink: '#172033'
  slate-muted: '#64748b'
typography:
  display:
    fontFamily: 'Space Grotesk, system-ui, Segoe UI, Roboto, sans-serif'
    fontSize: '3.25rem'
    fontWeight: 700
    lineHeight: 1.03
    letterSpacing: '-0.02em'
  headline:
    fontFamily: 'Space Grotesk, system-ui, Segoe UI, Roboto, sans-serif'
    fontSize: '1.375rem'
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: 'Inter, system-ui, Segoe UI, Roboto, sans-serif'
    fontSize: '1.0625rem'
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: 'Inter, system-ui, Segoe UI, Roboto, sans-serif'
    fontSize: '0.8125rem'
    fontWeight: 500
    letterSpacing: 'normal'
  caption:
    fontFamily: 'Inter, system-ui, Segoe UI, Roboto, sans-serif'
    fontSize: '0.75rem'
    fontWeight: 600
    letterSpacing: '0.04em'
rounded:
  sm: '8px'
  md: '12px'
  lg: '20px'
  xl: '28px'
  pill: '999px'
spacing:
  1: '4px'
  2: '8px'
  3: '12px'
  4: '16px'
  5: '24px'
  6: '32px'
  7: '48px'
  8: '64px'
components:
  button-primary:
    backgroundColor: 'linear-gradient(135deg, {colors.royal-blue}, {colors.studio-teal})'
    textColor: '{colors.pure-white}'
    rounded: '{rounded.lg}'
    padding: '0 64px'
    height: '3.5rem'
  button-primary-hover:
    backgroundColor: 'linear-gradient(135deg, {colors.royal-blue}, {colors.studio-teal})'
  input-field:
    backgroundColor: 'transparent'
    textColor: '{colors.near-black-ink}'
    rounded: '0'
    padding: '8px 0'
  input-field-focus:
    backgroundColor: '{colors.pure-white}'
    rounded: '{rounded.md}'
    padding: '8px 12px'
  card-bento:
    backgroundColor: '{colors.pure-white}'
    rounded: '{rounded.lg}'
    padding: '16px'
---

# Design System: AI Marketing Campaign Agent

## Overview

**Creative North Star: "The Draft Room"**

This is the visual language of a creative agency's draft room, not an enterprise dashboard: work is shown honestly at whatever stage it's actually in — a blank shot-grid, a row of skeleton bars standing in for copy that doesn't exist yet — rather than dressed up with a spinner or a stock icon pretending to be more finished than it is. The product's whole positioning (one brief in, one coherent campaign out, nothing final without human approval) shows up visually as restraint: one calm neutral canvas, one confident accent spent on the things that actually need it (the primary CTA, a selected state, a status badge), and a small, deliberate family of secondary/accent colors used to give each area (Campaign Brief, AI Output, individual platform choices) its own identity without the page turning into a rainbow.

The system is restrained, confident, and quietly colorful. It explicitly rejects two things: the generic enterprise SaaS dashboard (boxed sections, thick gray borders, everything the same visual weight) and the generic "AI-generated" UI cliché (purple/pink gradient blobs, spinning loaders, icon-tile grids standing in for real content). Depth is structural, not atmospheric — shadows exist to tell you something is actually layered above something else (the watch column overlapping the write column, a card lifting under hover/focus), not to add mood texture. Components stay unshowy by default (borderless inputs that only reveal their surface on focus, quiet section labels) so that the one place the system does get loud — the gradient CTA, a selected platform chip's glow, a status badge — actually reads as loud.

**Key Characteristics:**

- Draft-honest content: placeholders look like _drafts_ of their real shape (skeleton lines, blank frames, flat waveforms), never a generic spinner or icon standing in for unfinished work.
- One confident accent per moment: color is spent deliberately (the primary gradient CTA, a selected state, a status badge), not spread evenly across every element.
- Structural depth: shadow tiers exist to sell real layering (an element sitting _in front of_ another), not ambient decoration.
- Quiet chrome, loud state: resting UI (inputs, section labels) stays close to invisible; state changes (focus, selection, hover) are where the system allows itself contrast.

## Colors

A small neutral base carries the page; a five-color accent family (indigo/cyan-teal-emerald/coral/amber/sky) gives each functional area its own identity without the page reading as multicolored decoration.

### Primary

- **Royal Blue** (#2563EB): the system's one unmistakable action color — the primary CTA gradient's first stop, focus rings on every interactive element, links.
- **Royal Blue Deep** (#1D4ED8): hover/pressed state for Royal Blue surfaces.
- **Working Indigo** (#4F46E5): the Campaign Brief's identity color — section-legend icons, the platform-chip selected-state glow and border.

### Secondary

- **Bright Cyan** (#06B6D4): hero/platform accent variety; one stop in the "AI core" gradient concept.
- **Studio Teal** (#14B8A6): the AI Output Preview's identity color, paired with Confirming Emerald — the CTA gradient's second stop.
- **Confirming Emerald** (#10B981): the animated "selected" checkmark badge on platform chips; alternates with Studio Teal across bento preview tiles.

### Tertiary

- **Warm Coral** (#FB7156), **Signal Amber** (#F59E0B), **Sky Blue** (#38BDF8): the "mixed accent" set used specifically for platform-chip variety (one distinct color per platform) so the platform picker doesn't read as one repeated card. Signal Amber doubles as the status-badge color ("Coming after generation") — the one accent role deliberately _not_ shared with any icon-badge color, so a status chip always reads as status, not decoration.
- **Whisper Lavender** (#C4B5FD): used once, at low opacity, as a single small ambient touch — never a surface, never a gradient stop. The system's one intentionally "very subtle" color.

### Neutral

- **Paper White** (#F7FAFC) / **Cool Mist** (#EEF6FA): page background and its alternating tint — bright, soft-neutral, never pure white-on-white flatness.
- **Pure White** (#FFFFFF): card and panel surfaces, sitting one step brighter than the page background.
- **Near-Black Ink** (#172033): primary text.
- **Slate Muted** (#64748B): secondary/muted text (labels, descriptions, placeholders).
- **Hairline Blue** (`rgba(37, 99, 235, 0.08)`): the one divider color in the whole system, used only where a hairline is genuinely needed.

### Named Rules

**The One Accent Rule.** Any single moment on screen earns exactly one confident color decision — the CTA's gradient, a chip's selected glow, a status badge. Never stack two accent colors as competing focal points in the same component.

**The Status-Is-Not-Decoration Rule.** Signal Amber is reserved for status communication (the "Coming after generation" / asset-status badges). It is never reused as a decorative icon-badge color, so its appearance always means "this is telling you a state," not "this looked nice here."

## Typography

**Display Font:** Space Grotesk (with system-ui, Segoe UI, Roboto fallback)
**Body Font:** Inter (with the same fallback stack)

**Character:** A geometric, slightly technical display face paired with a highly legible, neutral body face — confident for headlines and section titles, quiet and workmanlike for the actual reading/writing surface (form labels, input text, card copy).

### Hierarchy

- **Display** (700, 3.25rem, line-height 1.03, letter-spacing -0.02em): the page's single H1 per route — "Create campaign," a Home headline, a Campaign Detail headline.
- **Headline** (600, 1.375rem): section/column titles ("Campaign brief," "AI output preview").
- **Body** (400, 1.0625rem): input text and primary reading copy — deliberately larger than a typical form's input size, so writing a brief reads as writing, not data entry.
- **Label** (500, 0.8125rem): field labels, section legends — small-caps-adjacent weight, quiet by default.
- **Caption** (600, 0.75rem, letter-spacing 0.04em): status badges, micro-metadata.

### Named Rules

**The Large-Input Rule.** Input text renders at body size (1.0625rem), never smaller than its own label implies — the form should read as prose you're writing, not a spreadsheet you're filling in.

## Layout

Single CSS Grid workspace per primary flow, not stacked boxed sections. `/campaigns/new`'s layout is the reference: a two-column grid (`minmax(0,1.15fr) minmax(0,0.85fr)`) from the very top of the page — no separate hero row above it. The right ("watch") column is `position: sticky` and pulled left over the write column's edge with a negative margin so the two halves share depth instead of sitting in clean parallel lanes. Below 960px the grid collapses to one column in natural reading order. Spacing rhythm uses the 4/8/12/16/24/32/48/64px scale consistently — generous gaps between major sections (48–64px), tighter gaps within a field group (8–16px).

## Elevation & Depth

Structural, not ambient: every _shadow_ exists to communicate that one surface sits above another, never as background texture alone. This is deliberately separate from the low-opacity blurred color washes each page uses behind its header (two soft `--glow-primary`/`--glow-emerald` circles) — that's brand-color presence on an otherwise neutral canvas, not a depth or elevation claim, and it is kept minimal on purpose (small radius, low opacity, tucked into a corner) so it never competes with the structural shadow system for meaning.

### Shadow Vocabulary

- **card** (`0 2px 8px rgba(23,32,51,0.06), 0 1px 2px rgba(23,32,51,0.04)`): resting elevation for bento/preview tiles.
- **float** (`0 24px 48px -18px rgba(37,99,235,0.14), 0 4px 12px rgba(23,32,51,0.04)`): hover-lift state for interactive cards.
- **float-hover** (`0 32px 56px -18px rgba(37,99,235,0.2), 0 6px 16px rgba(23,32,51,0.06)`): stronger hover state, used on the primary CTA.
- **hero** (`0 40px 72px -24px rgba(37,99,235,0.22), 0 8px 20px rgba(23,32,51,0.06)`): the largest tier — the sticky watch column's "sits in front of the form" shadow, and the CTA's resting shadow.

### Named Rules

**The Front-of-Form Rule.** The watch column's shadow tier is always at least one step heavier than anything in the write column, so it reads unambiguously as sitting in front, not beside.

## Shapes

Rounded, never sharp — the smallest radius in the system (8px) is still visibly soft. Radius scales with a surface's size and prominence: 8px for the smallest inline elements, 12px for inputs-on-focus and standard cards, 20px for section-level surfaces and bento tiles, 28px for the largest panels (the sticky watch column, the primary CTA), and a full pill (999px) for chips and badges. No hard borders as a default structural device — surfaces are separated by background-tint contrast and shadow, with hairlines (`--color-divider`) reserved for the rare case a literal line is clearer than spacing alone.

## Components

### Buttons

- **Shape:** fully rounded-large (20px), pill-adjacent but not a true pill — `--radius-lg`.
- **Primary:** `--gradient-brand` (Royal Blue → Studio Teal), white text, `--shadow-hero` at rest, a subtle top-highlight overlay (`::before`, white-to-transparent) for a glass-like sheen.
- **Hover / Focus:** lifts 2px, shadow steps up to a Royal-Blue-tinted glow (`float-hover` + `glow-primary`), arrow icon nudges forward; focus gets the standard 2px Royal Blue outline.
- **Pending state:** the arrow icon is replaced by a small spinning ring — the one place in the system a spinner is honest, because it reflects a real in-flight network request, not decorative "AI thinking."

### Cards / Containers (Bento preview tiles)

- **Corner Style:** 20px (`--radius-lg`).
- **Background:** a soft tone-tinted background per card (one of the five accent "-soft" tints), not uniform white — each tile's tone signals its family.
- **Shadow Strategy:** `card` at rest, `float` on hover, paired with a 2–4px lift and a slight de-rotation (tiles rest at a small intentional tilt, straighten on hover).
- **Content:** never an icon-plus-label alone — each tile shows the literal shape of its content (skeleton lines, caption bars, a blank shot-grid, a placeholder canvas, a flat waveform, a play-frame).

### Inputs / Fields

- **Style:** borderless and transparent at rest but for a single-pixel bottom hairline — closer to text-on-a-page than a boxed form field.
- **Focus:** reveals a filled white surface, 12px radius, and a soft Working-Indigo ring (`0 0 0 3px` indigo-soft) — the field visibly "arrives" only when you're using it.
- **Error:** bottom border (or full border, if focused) switches to `--color-danger`; message text renders in the same danger color directly beneath the field.

### Platform Chips

- **Style:** compact pill (`--radius-pill`), icon badge in a per-platform accent color (coral/indigo/sky/amber/emerald — each platform gets one, so the row reads as variety, not repetition), name text, visually-hidden-but-real checkbox input.
- **Selected state:** Working-Indigo background tint + border + a small Confirming-Emerald checkmark badge that scales/fades in — the one place selection state and the AI-Output-identity color (emerald) deliberately cross over, since "confirmed" is the same idea in both places.

### Disclosure (optional-field group)

- **Style:** native `<details>`/`<summary>`, no box — a small rotating triangle marker plus quiet muted-slate label text ("Add more detail"). Exists specifically to keep a ten-field form from reading as ten fields.

## Do's and Don'ts

### Do:

- **Do** show unfinished content as an honest draft of its own shape (skeleton bars, a blank shot-grid, a flat static waveform) — never a spinner, a generic icon, or a progress bar standing in for content that doesn't exist yet.
- **Do** spend shadow deliberately — a heavier tier means "this is structurally in front of that," not just "this looks nice."
- **Do** keep resting UI quiet (borderless inputs, small muted labels) so that state changes (focus, selection, hover, the CTA) are where contrast lives.
- **Do** give each functional area (Campaign Brief, AI Output, Platforms) its own accent identity from the established five-color family, rather than reusing Royal Blue everywhere.

### Don't:

- **Don't** use a spinning loader or fake progress indicator to represent AI "thinking" — the one exception is a real in-flight network request on the primary CTA.
- **Don't** introduce a sixth accent color or a new gradient combination without updating this file — the accent family (indigo/cyan/teal/emerald/coral/amber/sky) is closed by design, not by omission.
- **Don't** wrap every section in its own bordered/shadowed box by default — reach for spacing and typography first; a card boundary should mean something (a genuinely separate, liftable unit), not just "a new heading started."
- **Don't** use Whisper Lavender as a surface fill or gradient stop — it's a single subtle accent touch, not a fifth palette color to build with.
