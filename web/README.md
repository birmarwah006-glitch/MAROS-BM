# MAROS web

The new MAROS product UI. React + TypeScript + Vite + Tailwind.

This directory is **additive**. It does not modify the Python backend, and it
does not touch `MAROS/frontend/` or its `/app` and `/static` mounts — the
existing interface keeps working exactly as before.

## Running it

The backend must be up first, because this app talks to real endpoints and
mocks nothing:

```bash
# from the MAROS repo root
venv/bin/python -m uvicorn main:app --port 8000

# then, in another shell
cd web
npm install
npm run dev          # http://localhost:5173
```

### Why there is a dev proxy

The backend serves its API off the root path space (`/lectures`, `/modules`,
`/jobs`, ...) with no `/api` prefix, and `.env` pins
`CORS_ORIGINS=http://localhost:8000`. A Vite dev server on `:5173` is
therefore not an allowed origin.

Rather than change backend config, `vite.config.ts` proxies every API prefix
to `127.0.0.1:8000`. The browser only ever talks to its own origin, so CORS
never applies. In production FastAPI serves the built assets same-origin and
the identical relative paths work unchanged.

Point at a different backend with `MAROS_API=http://host:port npm run dev`.

## Checks

```bash
npm run typecheck   # tsc, no emit
npm run build       # production build
npm run smoke       # every route x mobile/tablet/desktop: console errors,
                    # failed requests, horizontal overflow, screenshots
npm run flow        # drives the real quiz and tutor flows against the backend
```

`npm run smoke` accepts `ROUTES=/a,/b` to narrow the sweep. Both scripts need
the dev server running.

## Ground rules

- **Never invent backend behaviour.** Every call goes to an endpoint that
  exists, with request and response shapes verified against a running server.
- **No mock data.** If a feature needs an endpoint that does not exist, it is
  documented in `docs/frontend-api-gaps.md` and left unbuilt rather than
  faked.
- **Design tokens are shared, not forked.** `src/styles/index.css` mirrors
  `frontend/style.css`, which `reel_planner.py` also reads its brand colours
  from. Change them in both places or neither.

## Documentation

Canonical docs live in the `reel` repository, which is where they are
reviewed:

- `docs/current-architecture.md` — the backend, its APIs, and what must not change
- `docs/frontend-plan.md` — approved decisions, stack, page-by-page bindings
- `docs/frontend-api-gaps.md` — every missing endpoint, and why it is needed

## Layout

```
src/
  api/        client.ts (fetch + auth), types.ts (verified shapes), hooks.ts
  components/ ui.tsx (design system), AppShell, Notes, Mermaid, Quiz,
              CodeRunner, TutorPanel, ReelPlayer
  pages/      Landing, Explore, Lecture, Lesson, AddLecture, Processing,
              MyLearning, Ask, Profile
  lib/        cn.ts, format.ts
scripts/      smoke.mjs, flow.mjs
```
