# Predictive Maintenance: frontend

A **read-only** operational dashboard (React + TypeScript) over the FastAPI backend in `../backend`. It shows system
health, registered model versions, alerts, engines and each engine's prediction history.

The data is a historical dataset (NASA C-MAPSS FD001) replayed through the backend's ingestion API by a simulator
process. It is not live physical sensor streaming. **Predicted RUL is the model's estimate, not the engine's actual
remaining useful life**, and the alert thresholds behind risk categories are demonstration values.

Status: implemented and automated-tested (MSW, no backend needed). Only the System page has been checked in a real
browser against a live backend; see [Verification status](#verification-status).

## Pages

| Route | Page | What it shows |
| --- | --- | --- |
| `/` | System | API health, readiness, simulation worker and runs, active model with metrics |
| `/engines` | Engines | Engines, 25 per page, with filters |
| `/engines/:engineId` | Engine detail | One engine, its latest prediction and its prediction history with an RUL chart |
| `/alerts` | Alerts | Alerts, newest first, 25 per page. **Default filter: Unresolved** (open or acknowledged) |
| `/models` | Models | Registered model versions, 10 per page; metrics grouped and collapsible, the active version's expanded |

Any other path shows "Page not found". `/simulation` is **not** a page (see [Not in the UI](#not-in-the-ui)).

Navigation (System, Engines, Alerts, Models), a skip link and per-page document titles are always present.

### Filters and pagination live in the URL

State is kept in query parameters, so reloading, bookmarking and the browser's Back button all work. Invalid values are
ignored and never sent to the API.

| Page | Parameters |
| --- | --- |
| Engines | `q` (digits only, at most 20; matches ids containing them), `risk` (`normal`, `warning`, `critical`), `alert` (`open`, `acknowledged`, `none`), `page` |
| Alerts | `status` (`unresolved` default, `open`, `acknowledged`, `resolved`, `all`), `severity` (`warning`, `critical`), `engine_id`, `page` |
| Models | `page` |

Changing a filter returns to page 1. Engines are listed **by engine id, in the API's order**; this is not a risk ranking
and the page says so (the API has no sort option). Engine ids in both lists link to the engine's detail page; the detail
page's "Back to engines" (or "Back to alerts") link returns to the list with the same filters and page.

## Engine detail

- **Overview:** data origin (**Simulated**, or **Ordinary ingestion** for engines that were not created by a simulation
  run), risk of the latest prediction, unresolved alert, last cycle and reading time, readings and predictions stored,
  first seen, and a link to the engine's alerts.
- **Latest prediction:** predicted RUL (cycles), cycle, model version, failure score (uncalibrated, not a probability).
- **Prediction history:** the **latest 200 predictions by cycle** (the API's maximum page), newest first in a table. A note
  says either "Showing all N stored predictions" or "Showing the latest 200 of N ... not the engine's complete history".
- **RUL chart:** inline SVG (no chart library) of predicted RUL against cycle. Every prediction is plotted; a cycle can
  hold several predictions when different model versions predicted it, and none are merged. Each model version has its own
  marker shape and line pattern, named in a legend; marker colour is the risk category the API reported. No thresholds or
  reference lines are drawn.
- **Invalid ids** (anything but a positive whole number of up to 9 digits) show "Invalid engine id" and make no request.
  An unknown engine shows "not found".

## Backend endpoints used (all `GET`, no credentials)

| Endpoint | Used by |
| --- | --- |
| `/health`, `/ready` | System |
| `/api/v1/simulation` | System (worker and run status) |
| `/api/v1/models/active` | System |
| `/api/v1/models` | Models |
| `/api/v1/engines` | Engines |
| `/api/v1/engines/{id}`, `/api/v1/engines/{id}/predictions` | Engine detail (`order=desc&limit=200&offset=0`) |
| `/api/v1/alerts` | Alerts |

Schemas are validated with Zod at the boundary (`src/api/schemas.ts`, mirroring `backend/app/schemas/api.py`); unknown
extra fields are ignored. Requests are `GET` with `credentials: 'omit'` and only an `Accept` header.

### Loading, error and empty behaviour

- **Loading, API unreachable** (network failure or a gateway 502/504), **backend error** (the `{error:{code,message,details}}`
  envelope), **unexpected response** (validation failed), and **empty / no match / past the last page** states, each worded
  differently. Error alerts offer **Try again**.
- `/ready` answering 503 with a normal body is shown as "Not ready", not as an outage. A 404 `not_found` on the active
  model is the "no active model" state; other 404s stay errors.
- While a new filter or page loads, the previous results stay on screen dimmed with an "Updating..." line, and the pager
  describes the rows actually shown. If a refresh fails, the old data stays with a "Showing the last successful result"
  notice.
- Only the **System page polls** (health and readiness every 15 s, simulation 5 s, model 60 s; an Auto-refresh checkbox
  turns it off, and it pauses while the tab is hidden). Every other page, including Engine detail, loads once and
  re-requests only when you press **Refresh**.
- Transient failures (network, 5xx) are retried once; 4xx responses and contract violations are not.

## Not in the UI

**Backend functionality that exists but has no frontend controls**

- Ingestion (`POST /api/v1/readings`) and the dry-run `POST /api/v1/predict`.
- **Alert acknowledgement** (`POST /api/v1/alerts/{id}/acknowledge`). It needs the same `API_KEY` that authorises ingestion and
  the simulation worker, which the browser must never hold. The Alerts page is read-only and says so.
- Simulation controls (start, stop, change speed) and the worker protocol.
- `GET /api/v1/engines/{id}/readings` (stored sensor readings) and the simulation run history endpoints
  (`/api/v1/simulation/runs`, `/runs/{id}`).

**Deferred, with the reason**

- **Simulation history page:** the backend's simulation `GET` endpoints apply lazy run expiry on the server (a database
  write), so a read-only page needs a read-only backend query path first. The System page already calls
  `/api/v1/simulation` (that is existing behaviour).
- **Simulation controls and alert acknowledgement** (planned as separate milestones): the design is to ask the operator for
  the control key at runtime and keep it in React memory only; acknowledgement needs a backend authentication decision.
- Backend sorting and fleet summary endpoints (for a true "worst engines first" view).

**Not implemented anywhere in the project:** drift monitoring, retraining, model promotion and rollback.

## Setup and development

Prerequisites: Node.js `^22.22.2`, `^24.15.0` or `>=26` (enforced by `engines` and `engine-strict`) and npm. For real data,
run the backend as described in `../docs/PROJECT_STATUS.md` (section "Commands"); by default it listens on
`http://127.0.0.1:8000`.

Run from `frontend/`:

```bash
npm ci               # install exactly what the lockfile says
npm run dev          # dev server on http://localhost:5173
npm run build        # type-check (tsc -b), then production build into dist/
npm run preview      # serve dist/ on http://localhost:4173 (same proxy as dev)
npm run typecheck    # tsc -b
npm run lint         # eslint .
npm test             # vitest run: MSW-mocked, needs no backend or database
npm run test:watch
```

### How the frontend reaches FastAPI

Application code only calls **same-origin relative URLs** (`/health`, `/ready`, `/api/v1/...`). The Vite dev and preview
servers proxy them to the backend (`vite.config.ts`):

| Browser requests | Forwarded to |
| --- | --- |
| `/api/*` | `${BACKEND_URL}/api/*` |
| `/health`, `/ready` | `${BACKEND_URL}/health`, `${BACKEND_URL}/ready` |

`BACKEND_URL` defaults to `http://127.0.0.1:8000`. To point elsewhere, set it in the shell that starts Vite:

```bash
BACKEND_URL=http://127.0.0.1:9000 npm run dev            # bash
```

```powershell
$env:BACKEND_URL = 'http://127.0.0.1:9000'; npm run dev  # PowerShell
```

It is read only by the Vite config (Node), is not a `VITE_` variable, and never reaches the browser bundle. Requests are
same-origin, so CORS is not involved. Ports 5173 and 4173 are fixed (`strictPort`). A production deployment needs a reverse
proxy with the same routes in front of the static files, and it must also serve `index.html` for client-side routes such as
`/engines/24`; neither is configured in this repository.

### Backend requirements

- The simulation, engine and readings tables and columns come from migration `0002`. On a database still at `0001`
  (as `pm_dev` is today) `GET /api/v1/simulation` returned HTTP 500 when checked live, and the System page shows that as a
  backend error. The engine endpoints read `engines.origin`, which is also added by `0002`, so the Engines, Engine detail
  and Alerts pages are expected to need a database at `0002` (not checked live).
- `/ready` reports "not ready" while the database is behind the latest migration.

## Layout

```
src/
  api/         fetch client (GET only), endpoint functions, Zod schemas, error types
  query/       TanStack Query hooks, retry and polling policy
  pages/       System, Models, Alerts, Engines, Engine detail, not found
  components/  tables, pager, filters, status badges, RUL chart, metric groups, page states
  lib/         pure helpers: chart geometry, URL parameter parsing, pagination, formatting
  test/        MSW server and fixtures, shared test backends, render helper, Vitest setup
```

## Accessibility and responsive behaviour

- Skip link, one `h1` per page, a distinct document title per page, and a polite live region that announces the new page
  (and moves focus to the main content) after navigating to another page. Changing a filter or page does not move focus.
- Result counts are announced politely ("60 engines found. Showing 1 to 25."); `aria-busy` wraps only the displayed
  results, never the loading message or an error alert.
- Tables are labelled, keyboard-focusable scroll regions with captions and column headers; status is always text, never
  colour alone. Filters are native labelled controls.
- The chart is `role="img"` with a title and a plain-language description, and its data is in the adjacent table.
- Layouts reflow to one column on narrow screens; tables and the chart scroll sideways instead of shrinking.

## Security boundaries

- No `API_KEY` or `CONTROL_API_KEY` exists in the frontend; it calls only public, read-only endpoints and sends no
  credential headers or cookies.
- No secrets in `VITE_*` variables (none are used). When simulation controls arrive in a later milestone, the control key
  will be typed by the operator at runtime and held in React memory only (cleared on reload), per
  `../docs/decisions/0001-simulation-control-plane.md`.
- ESLint forbids `localStorage`, `sessionStorage`, `document.cookie` and `dangerouslySetInnerHTML` in application code, and a
  test asserts the System page leaves storage and cookies empty.
- Error messages show status, code and message only, never request headers. Router history state used for back links is
  validated and ignored if malformed.
- Tests use MSW; unhandled requests fail the test, so nothing can reach a real backend or database.

## Verification status

- **Automated:** 444 tests in 19 files (Vitest, Testing Library, MSW), plus type-check, lint and a production build, all
  passing on the last run. Several key behaviours were mutation-checked (the tests fail when the behaviour is broken).
- **Real browser, live backend:** the System page (including grouped model metrics and the not-ready, unreachable and error
  states) was checked in a real browser against a running backend on a database at migration `0001`.
- **Not verified in a live browser:** Models, Alerts, Engines and Engine detail pages, the RUL chart, and the skip link,
  focus and live-region behaviour. No assistive technology has been used, and the chart's appearance at small sizes is
  unchecked.

## Known limitations

- Prediction history shows only the **latest 200 predictions**; earlier ones are not reachable from the UI.
- The chart reuses marker shapes after **five model versions** (the legend, hover text and table still name every
  version).
- Engine detail does not poll; use Refresh.
- Engines cannot be sorted or ranked by risk (the API orders by id); search matches ids that contain the digits.
- Timestamps use the browser's locale and time zone, without a zone label.
- `index.html` keeps a generic title until the app loads.
- The pager stays enabled while a new page loads and acts on the rows shown, so after a filter change it can lead to a page
  that does not exist under the new filter (the "past the end" state then explains it).
- Alerts and engines have no streaming or push updates.
- The repository has no root README; project-wide commands and status are in `../docs/PROJECT_STATUS.md`.
