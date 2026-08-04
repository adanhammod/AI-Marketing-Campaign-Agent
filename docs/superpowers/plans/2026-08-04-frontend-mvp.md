# Frontend MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task, once its architecture is approved and Phase 2 (the code-level task breakdown) is written. This document is the architecture/scoping pass; it deliberately contains no code, per explicit instruction.

**Goal:** Stand up a React/TypeScript SPA that lets the single demo user create a campaign, watch it progress, and preview results, fully wired against the 3 API endpoints that exist today — structured so that lifecycle actions (approve/revise/retry/cancel/download) slot in later without rework once Group C ships.

**Architecture:** Vite + React + TypeScript SPA, served as its own deployable (matching `services/api`, `services/worker`, `services/marketing-mcp`). TanStack Query owns all server state and polling against a typed `fetch` client generated from the FastAPI OpenAPI schema — the same contract-first discipline the backend already uses, just consumed from the other side.

**Tech Stack:** Vite, React 18, TypeScript, React Router, TanStack Query (`@tanstack/react-query`), `openapi-typescript` (codegen, dev-only), Vitest + React Testing Library + MSW (mock service worker) for tests. No SSR framework, no Redux/Zustand, no axios — justified below.

## Global Constraints

- `snake_case` JSON, UTC ISO-8601 timestamps, UUID IDs — consume API responses as-is, no field renaming at the client boundary (`docs/contracts/api-contracts.md`).
- Mutation endpoints require an `Idempotency-Key` header (not relevant yet — the only mutation endpoint live today, `POST /campaigns`, needs one).
- Clients must not derive legal actions independently — always drive UI affordances off `CampaignDetailResponse.actions` (a `dict[str, str]` of action name → URL) and `status`, never off client-side status-transition logic (`docs/contracts/api-contracts.md` §Polling Behavior, verbatim).
- Polling: begins near 2s, backs off to at most 10s, does not overlap, pauses when appropriate, stops at terminal state (`docs/spec.md` §20; exact per-status rules in `docs/contracts/api-contracts.md` §Polling Behavior).
- No scope expansion beyond what's below — matches your standing instruction from the Week 2 backend plan, carried over to frontend.

---

## 1. API Contract Review

Read directly from `services/api/src/campaign_api/routers/campaigns.py` and `shared/src/campaign_contracts/api.py` (the same Pydantic package the worker consumes — this is the frontend's equivalent source of truth) and the normative `docs/contracts/api-contracts.md`.

### 1a. Endpoints that exist today

| Method | Path | Response | Notes |
|---|---|---|---|
| `POST` | `/api/v1/campaigns` | `202` `CampaignCreationAcceptedResponse` | Requires `Idempotency-Key` header. Body is `CampaignCreationRequest` (= `NormalizedCampaignBrief`). |
| `GET` | `/api/v1/campaigns` | `200` `CampaignListResponse` | `offset`/`limit` query params (contract doc says cursor-based; current implementation is offset-based — flagged below as a real drift worth resolving before History ships against it). |
| `GET` | `/api/v1/campaigns/{campaign_id}` | `200` `CampaignDetailResponse` | No lifecycle actions can be exercised yet, but the full read-model (strategy/copy/storyboard/artifacts/error/`actions`) is already there once a campaign has run through Group A's now-working pipeline. |

### 1b. Endpoints that do not exist yet (Group C)

`GET /campaigns/{id}/events`, `GET /campaigns/{id}/artifacts`, `POST .../approve`, `POST .../revisions`, `POST .../retry`, `POST .../cancel`. Exact request/response shapes are already frozen in `docs/contracts/api-contracts.md` and `shared/campaign_contracts/api.py` (`RevisionRequest`, `RetryResponse`, `CancellationResponse`, etc.) — genuinely useful now, because the frontend's typed client and component props can be built against these shapes today even though the routes 404, using MSW-mocked handlers in tests and a feature-flagged/disabled state in the UI.

### 1c. Screen readiness (spec §20's 6 screens)

| Screen | Buildable now? | Why |
|---|---|---|
| **Creation** | ✅ Yes, fully | `POST /campaigns` exists; form fields map 1:1 to `NormalizedCampaignBrief`. |
| **Progress** | ✅ Yes, fully | `GET /campaigns/{id}` returns `status`/`current_step`/`progress_percent`; polling contract is fully specified. |
| **Result** | ⚠️ Partially | Text/storyboard/image/video **display** works now (`CampaignDetailResponse.strategy/copy/storyboard/artifacts`). Approve/Revise/Retry/Download **actions** are blocked on Group C — build the display now, stub the action buttons as disabled with a "coming soon" affordance driven by the (currently empty) `actions` map. |
| **History** | ✅ Yes, fully | `GET /campaigns` exists and returns `CampaignSummary` items. Pagination is offset/limit-based (per ADR-021; see §9) — simple page controls, not cursor/infinite-scroll. |
| **Error** | ✅ Yes, fully | `CampaignDetailResponse.error` (`SanitizedWorkflowError`) and `retry_eligible` are already populated by `handle_failure` (Task 20) once a real run fails. Retry *action* is blocked on Group C, but the error display itself is not. |
| **Empty** | ✅ Yes, fully | No data dependency — pure UI state when `GET /campaigns` returns zero items. |

**Net effect: 5 of 6 screens are fully buildable today, 1 (Result) is partially buildable with the action-affordance seams left open for Group C.** This is exactly the shape your Week 2 plan anticipated when it decided frontend didn't need to wait.

---

## 2. Proposed React Architecture

**Vite, not Next.js.** This is a client-only SPA sitting behind a REST API with no SSR/SEO requirement (spec explicitly frames it as an internal demo tool for one user) — Next.js's server components, file-based routing, and build complexity would add nothing here and cost real setup/debugging time. Vite is the standard, minimal choice for exactly this shape of app.

**React Router** for the 6 screens (`/`, `/campaigns/new`, `/campaigns/:id`, `/campaigns` history list) — nothing exotic needed.

**TanStack Query, not a hand-rolled polling hook.** Its `refetchInterval` accepts a function of the last query result, which maps directly onto the spec's polling rules: return `2000`–`10000` (backoff) while non-terminal, a larger number or `false` at `READY_FOR_REVIEW` (manual refresh), and `false` at `FINAL`/`REVISION_REQUESTED`/`FAILED`/`CANCELLED` (stop). TanStack Query also natively dedupes in-flight requests (satisfies "does not overlap") and pauses refetch when the tab is backgrounded by default (satisfies "pauses when appropriate"). Writing this by hand would just be reimplementing what the library already does correctly.

**No Redux/Zustand.** Nearly all state in this app *is* server state (campaign data), which TanStack Query already owns as a cache. The only client-only state is transient UI state (form field values, a modal being open) — that's a handful of `useState`/`useReducer` calls per component, not a cross-cutting concern that justifies a global store. Add one later only if a real need appears (e.g., cross-screen draft persistence) — YAGNI for the MVP.

**Native `fetch`, not axios.** The typed client (below) is a thin wrapper; `fetch` is sufficient and avoids an unnecessary dependency for an app this size.

---

## 3. Proposed Folder Structure

Lives at `services/frontend/`, alongside `services/api`, `services/worker`, `services/marketing-mcp` — consistent with the existing "every deployable lives under `services/`" convention and spec §21's Kubernetes deployment list, which already names the React frontend as its own workload.

```
services/frontend/
  src/
    api/
      client.ts            # thin typed fetch wrapper (uses generated types)
      schema.gen.ts         # generated by openapi-typescript -- never hand-edited
      queries/
        campaigns.ts        # TanStack Query hooks: useCampaignList, useCampaignDetail, useCreateCampaign
    routes/
      CreateCampaignPage.tsx
      CampaignListPage.tsx
      CampaignDetailPage.tsx
      NotFoundPage.tsx
    components/
      creation/
        CampaignForm.tsx
      progress/
        StatusStepper.tsx
        ElapsedTime.tsx
      result/
        StrategySection.tsx
        CopySection.tsx
        StoryboardSection.tsx
        ArtifactGallery.tsx
        ActionBar.tsx        # renders buttons from CampaignDetailResponse.actions; disabled until Group C
      history/
        CampaignSummaryRow.tsx
      shared/
        ErrorPanel.tsx        # renders SanitizedWorkflowError + retry_eligible
        EmptyState.tsx
        LoadingSkeleton.tsx
    lib/
      polling.ts             # refetchInterval policy function (pure, unit-testable)
      status.ts              # CampaignStatus -> stepper stage / terminal? helpers
    App.tsx
    main.tsx
  tests/
    unit/
    integration/             # MSW-mocked API, per-screen render tests
  scripts/
    generate-schema.mjs       # runs the OpenAPI export + openapi-typescript codegen
  package.json
  vite.config.ts
  tsconfig.json
  .env.example
```

---

## 4. Proposed Component Hierarchy

```
App
 └─ Router
     ├─ CreateCampaignPage
     │   └─ CampaignForm            (controlled form, client + server validation surfaced inline)
     ├─ CampaignListPage
     │   ├─ EmptyState              (shown when items = [])
     │   └─ CampaignSummaryRow[]    (links into CampaignDetailPage)
     └─ CampaignDetailPage
         ├─ StatusStepper           (progress screen concern)
         ├─ ElapsedTime
         ├─ ErrorPanel              (shown when status = FAILED / error != null)
         ├─ StrategySection         \
         ├─ CopySection              |  result screen concerns -- each independently
         ├─ StoryboardSection        |  loading/error per spec's "section-level loading/errors"
         ├─ ArtifactGallery         /
         └─ ActionBar                (approve/revise/retry/cancel/download -- disabled today)
```

`CampaignDetailPage` is the single screen that covers both spec's "Progress" and "Result" rows — they're the same route at different `status` values, not different pages, matching how the API itself models it (one `CampaignDetailResponse` shape throughout the lifecycle).

---

## 5. State Management Approach

- **Server state:** 100% TanStack Query. Query keys: `["campaign", id]`, `["campaigns", {offset, limit}]`. Mutations (`useCreateCampaign`, later `useApprove`/`useRetry`/etc.) use `useMutation` + cache invalidation of the relevant `["campaign", id]` key on success.
- **Client-only state:** local component state only (form inputs, disabled/loading flags derived from mutation status). No global store.
- **URL as state:** `campaign_id` and `version` (when we later support viewing non-current versions) live in the route path/query string, not in a store — makes detail pages shareable/refreshable by construction, and matches the contract's "a higher `current_version` causes navigation/refresh to that version" rule directly (react-router navigation, not client state juggling).

---

## 6. Polling Strategy

Implemented as one pure function `lib/polling.ts`, unit-tested in isolation (input: last `CampaignDetailResponse`, output: next interval in ms or `false`), then passed as TanStack Query's `refetchInterval`:

| `status` | Behavior |
|---|---|
| `CREATED`, `QUEUED`, `GENERATING_*`, `RENDERING_VIDEO` | Poll, starting near 2000ms, backing off toward 10000ms the longer it stays in a non-terminal state (matches spec's "backs off to at most 10" — implemented as a capped exponential/linear ramp, not a fixed interval). |
| `READY_FOR_REVIEW` | Stop automatic polling; expose a manual "Refresh" affordance instead (spec: "switches to slow/manual refresh while awaiting user action"). |
| `FINAL`, `REVISION_REQUESTED`, `FAILED`, `CANCELLED` | Stop polling entirely (terminal). |

"Does not overlap": TanStack Query will not start a new fetch for the same key while one is in flight — native behavior, not something we implement. "Pauses when appropriate": `refetchIntervalInBackground: false` (default) already pauses when the browser tab isn't visible.

Events (`GET /campaigns/{id}/events`, once it exists) are explicitly **not** polled independently for the MVP — the contract says the detail response's `current_version`/`event_sequence` already tells the client when to refresh; a separate events feed is a Group C-era enhancement, not required for the MVP screens.

---

## 7. Generated Schema/Type Consumption

Mirrors the backend's own contract-first approach (`shared/campaign_contracts`), just from the other side of the same FastAPI app:

1. `scripts/generate-schema.mjs` (or a small Python one-liner using the existing `campaign_api.main.app` object's `.openapi()` — no live server needed, matching how `tests/test_health_openapi.py` already introspects the schema in-process) dumps the current OpenAPI document to `openapi.json`.
2. `openapi-typescript openapi.json -o src/api/schema.gen.ts` generates a single TypeScript declarations file covering every request/response/component shape currently in `shared/campaign_contracts` — `CampaignCreationRequest`, `CampaignDetailResponse`, `RetryResponse`, etc. all become TS types with zero hand-written duplication.
3. `src/api/client.ts` is a thin `fetch` wrapper typed against `schema.gen.ts` (e.g. via `openapi-fetch`, a small companion library, or a hand-written 20-line generic — decide at implementation time, doesn't change the plan).
4. `schema.gen.ts` is **regenerated, never hand-edited** — checked into git so CI/local dev doesn't require a live backend to type-check, but re-run via the script whenever `shared/campaign_contracts` changes. This is the direct frontend analogue of `campaign-contracts` being a shared, versioned package the backend services all depend on.

This means when Group C ships `POST .../approve` etc., the frontend gains their exact types for free by re-running step 1–2 — no manual interface-writing, no drift.

---

## 8. What ships in this pass vs. later

**Buildable and in scope now (once this plan is approved):**
- Project scaffold, routing, typed API client + codegen pipeline, environment config, linting (matches `docs/plan.md`'s own Frontend checklist item 1).
- Creation screen (full).
- Progress + Result display (full, actions disabled).
- History/List screen (full; offset/limit pagination per ADR-021, see §9).
- Error and Empty screens (full).
- Component + MSW-mocked integration tests for all of the above.

**Explicitly deferred, not started now:**
- Approve/Revise/Retry/Cancel/Download actions — UI seams exist (`ActionBar`, disabled) but wiring waits for Group C's real endpoints.
- Events feed UI.
- Any global client-state library — add only if a real cross-screen need appears.

---

## 9. Pagination — resolved

`docs/contracts/api-contracts.md` specifies `GET /campaigns` as cursor-paginated (`next_cursor`), but `services/api/src/campaign_api/routers/campaigns.py:40-47` implements `offset`/`limit`, per ADR-021's approved decision. **Resolved 2026-08-04: the frontend builds against the actual offset/limit implementation.** The contract doc's drift is tracked as a deferred documentation follow-up in `docs/plan.md`'s "Explicitly deferred" section — not fixed as part of this work, and the backend implementation is not touched. History screen pagination (Task F3, when scoped) uses simple page-number/offset controls, not cursor-based infinite scroll.

---

## Phase 2: Task Breakdown

Same discipline as the backend Weeks 1–2: one task, one commit, tests first, stop for review. AI-assisted UX features (natural-language brief, auto-fill, clarification questions, recommendations) are explicitly out of scope for every task below — second iteration, after this MVP.

- **Task F1 — Project scaffold.** `services/frontend/`: Vite + React + TypeScript init, ESLint + Prettier, `vite.config.ts`, `tsconfig.json`, React Router installed with an empty route table, `.env.example` (`VITE_API_BASE_URL`), Vitest + React Testing Library + MSW installed and configured, a trivial smoke test. No screens yet.
- **Task F2 — Typed API client + schema codegen.** `scripts/generate-schema.mjs` (or Python export script) dumping `campaign_api.main.app.openapi()` to JSON; `openapi-typescript` wired as a dev script generating `src/api/schema.gen.ts`; `src/api/client.ts` thin typed `fetch` wrapper; `src/api/queries/campaigns.ts` with `useCreateCampaign`, `useCampaignList`, `useCampaignDetail` TanStack Query hooks against the 3 live endpoints. Tests: MSW-mocked hook tests proving each query/mutation calls the right method/path and parses the typed response.
- **Task F3 — Create Campaign screen.** `CampaignForm` (all `NormalizedCampaignBrief` fields, client-side validation matching the shared contract's field constraints), `CreateCampaignPage`, `Idempotency-Key` generation on submit, success navigates to the new campaign's detail route, server-side 422 errors surfaced inline per field. Tests: form validation, successful submit navigates, server error renders inline.
- **Task F4 — Campaign Detail: Progress + polling.** `CampaignDetailPage` (route `/campaigns/:id`), `lib/polling.ts` (pure function, unit-tested standalone against every `CampaignStatus` value per §6's table), `StatusStepper`, `ElapsedTime`, wired to `useCampaignDetail` with `refetchInterval: pollingIntervalFor(data)`. Tests: polling function's interval/stop decisions per status; component renders correct stage.
- **Task F5 — Campaign Detail: Result display.** `StrategySection`, `CopySection`, `StoryboardSection`, `ArtifactGallery` (images/video preview via `PublicArtifactReference.download_url` when present), `ErrorPanel` (renders `SanitizedWorkflowError` + `retry_eligible`), `ActionBar` (renders from `CampaignDetailResponse.actions`; empty map today so nothing renders yet — no hardcoded buttons). Tests: each section's loading/populated/absent states independently (matches spec's "section-level loading/errors").
- **Task F6 — History/List screen.** `CampaignListPage`, `CampaignSummaryRow`, offset/limit page controls (per §9's resolution), `EmptyState` when `items = []`. Tests: pagination controls, empty state, row navigation to detail.
- **Task F7 — Cross-cutting polish.** Loading skeletons, responsive layout pass, baseline accessibility (semantic landmarks, labeled inputs, focus management on navigation) across all screens built so far. Tests: accessibility assertions (e.g. `jest-axe`/`vitest-axe` or RTL role queries) per screen.

Task order is sequential (F2 depends on F1; F3–F6 each depend on F2; F7 depends on F3–F6 existing). Stopping for review after each, exactly as Weeks 1–2.

## Execution Handoff

This is the architecture/scoping pass, deliberately code-free per your instruction. Approved 2026-08-04, including the AI-assisted UX features (natural-language brief input, auto-fill, clarification questions, recommendations) explicitly deferred to a second iteration after this MVP — the architecture above (typed API client, TanStack Query server-state layer, component boundaries) is intentionally generic enough that a future AI-assisted brief flow can populate `CampaignForm`'s existing fields/mutation without restructuring this layer. Next step: a `superpowers:writing-plans` pass turning Section 8's "buildable now" list into the same bite-sized TDD task breakdown (Files/Interfaces/Steps/Tests) used for every backend task in Weeks 1–2 — one task per screen/concern, one commit each, same review cadence.
