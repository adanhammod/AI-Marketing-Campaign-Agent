# AI Marketing Campaign Agent — Frontend

React/TypeScript SPA for the campaign control plane, built with Vite. See
`docs/superpowers/plans/2026-08-04-frontend-mvp.md` for the approved architecture and task
breakdown.

## Development

```bash
npm install
cp .env.example .env
npm run dev
```

The dev server proxies `/api` to `http://localhost:8000` (the FastAPI control plane) — no CORS
configuration needed locally.

## Scripts

- `npm run dev` — start the dev server
- `npm run build` — type-check and produce a production build
- `npm run test` / `npm run test:watch` — run the Vitest suite
- `npm run lint` — oxlint
- `npm run format` / `npm run format:write` — check/apply Prettier formatting
