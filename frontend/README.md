# Predictive Maintenance: frontend

React + TypeScript dashboard for the FastAPI backend in `../backend`. Current scope (Phase 3, milestones M0 + M1): the
scaffold, a typed read-only API layer and the **System** page (API health, readiness, simulation worker/run status,
active model). There is no authentication and there are no simulation controls yet.

## Stack

React 19, TypeScript 6.0, Vite 8, Tailwind CSS 4, React Router 8, TanStack Query 5, Zod 4, Vitest 5, Testing Library,
MSW 3, ESLint 10. Versions are pinned exactly (`.npmrc` has `save-exact=true`) and `package-lock.json` is committed.
TypeScript is held at 6.0.x because typescript-eslint does not yet support TypeScript 6.1 or later.

## Prerequisites

- Node.js `^22.22.2`, `^24.15.0` or `>=26` (enforced by `engines` and `engine-strict`; jsdom 31 needs it)
- npm
- For real data: the backend running locally (see `../README.md`), by default on `http://127.0.0.1:8000`

## Commands

Run from `frontend/`:

```bash
npm ci               # install exactly what the lockfile says
npm run dev          # dev server on http://localhost:5173
npm run build        # type-check (tsc -b) then production build into dist/
npm run preview      # serve dist/ on http://localhost:4173 (same proxy as dev)
npm run typecheck    # tsc -b
npm run lint         # eslint .
npm test             # vitest run (MSW-mocked; needs no backend or database)
npm run test:watch
```

## How the frontend reaches FastAPI

Application code only calls **same-origin relative URLs**: `/health`, `/ready`, `/api/v1/...`. The Vite dev and preview
servers proxy these to the backend (`vite.config.ts`):

| Browser requests        | Forwarded to                      |
| ----------------------- | --------------------------------- |
| `/api/*`                | `${BACKEND_URL}/api/*`            |
| `/health`, `/ready`     | `${BACKEND_URL}/health`, `/ready` |

`BACKEND_URL` defaults to `http://127.0.0.1:8000`. To point elsewhere, set it in the shell that starts Vite:

```bash
BACKEND_URL=http://127.0.0.1:9000 npm run dev
```

It is read only by the Vite config (Node), never prefixed `VITE_`, and never reaches the browser bundle. Because requests
are same-origin, CORS is not involved. A production deployment must put a reverse proxy with the same routes in front of
the static files; `dist/` contains no backend address.

## System page

Four independent queries, so one failing endpoint never blanks the others:

| Card         | Endpoint                  | Poll  | Notes                                                              |
| ------------ | ------------------------- | ----- | ------------------------------------------------------------------ |
| API health   | `GET /health`             | 15 s  |                                                                    |
| Readiness    | `GET /ready`              | 15 s  | HTTP 503 with a `ReadyOut` body is shown as **Not ready**, not as an outage |
| Simulation   | `GET /api/v1/simulation`  | 5 s   | worker online/offline, workers online, active run, last run        |
| Active model | `GET /api/v1/models/active` | 60 s | 404 means "no active model" (empty state); only numeric metrics actually returned are shown |

States are distinguished: loading, **API unreachable** (network), **backend error** (`{error:{code,message,details}}`
envelope), **unexpected response** (Zod validation failed) and the empty states. Polling can be switched off with the
Auto-refresh checkbox, pauses automatically while the tab is hidden, and there is a manual Refresh button. Transient
failures (network, 5xx) are retried once; 4xx and contract violations are not.

Responses are validated with Zod at the boundary (`src/api/schemas.ts`, mirroring `backend/app/schemas/api.py`).
Unknown extra fields are ignored; no fields are invented.

## Layout

```
src/
  api/        client (fetch), endpoints, Zod schemas, error types
  query/      TanStack Query hook, polling and retry policy
  pages/      SystemPage, NotFoundPage
  components/ shared UI (cards, badges, error notice)
  lib/        formatting and metric extraction
  test/       MSW server, fixtures, Vitest setup
```

## Security boundaries

- No `API_KEY` or `CONTROL_API_KEY` exists anywhere in the frontend; this milestone calls only public, read-only endpoints
  and sends no credential headers.
- No secrets in `VITE_*` variables (none are used). When simulation controls arrive in a later milestone, the control key
  will be typed by the operator at runtime and held in React memory only (cleared on reload), per
  `docs/decisions/0001-simulation-control-plane.md`.
- ESLint forbids `localStorage`, `sessionStorage`, `document.cookie` and `dangerouslySetInnerHTML` in application code; a
  test asserts the System page leaves storage and cookies empty.
- Error messages show status, code and message only, never request headers.
- Tests use MSW; unhandled requests fail the test, so nothing can reach a real backend or database.
