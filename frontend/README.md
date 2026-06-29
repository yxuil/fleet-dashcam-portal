# Dashcam Portal — Frontend

React + TypeScript + Vite frontend for the fleet dashcam portal MVP.

## Quick start

```bash
npm ci          # clean install from lockfile
npm run dev     # start Vite dev server (http://localhost:5173)
npm run build   # type-check + production bundle (dist/)
npm test        # run Vitest unit tests
npm run e2e     # run Playwright end-to-end tests
npm run lint    # oxlint
```

Backend (FastAPI) is expected at `http://localhost:8000`; see `vite.config.ts` for the dev proxy.

For the full stack (backend, MinIO, Postgres, Docker compose), see the [root README](../README.md).
