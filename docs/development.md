# Development

## Repository layout

```
backend/            FastAPI app (uv project)
  app/
    agents/         pydantic-ai agents, tools, runner
    api/            routes, schemas, pagination, presenters, errors
    db/             models, engine, migrations runner
    llm/            model factory, OCR pipeline, timing wrapper
    paperless/      HTTP client, schemas, taxonomy registry
    proposals/      proposal schemas, apply engine, journal
    services/       step engine, pipeline, jobs, audit, prefs, …
  alembic/          migrations (squashed pre-1.0)
  tests/            unit / integration / live
frontend/           React + Vite + TS (npm project)
  src/api/          generated OpenAPI types (npm run gen:api)
  src/components/   shared components (+ vendored shadcn/ui)
  src/features/     session surface
  src/pages/, src/hooks/, src/lib/
deploy/             Containerfile, production & playground compose
docs/               this site (mkdocs)
```

## Backend

```bash
cd backend
uv sync
uv run pytest -q                 # unit tests (no services needed)
uv run paperless-llm serve       # dev server on :8100
```

- **Unit tests** mock paperless with respx and the LLM with
  pydantic-ai's `FunctionModel` — fast and deterministic.
- **Integration tests** (`tests/integration`) expect a disposable
  paperless instance (`deploy/test/compose.yaml`).
- **Live tests** (`pytest -m live_llm`) talk to a real local LLM —
  never run in CI; they exist to validate model behavior end to end.

The migration chain is pinned to the models by
`tests/unit/test_migrations.py` — if you change `db/models.py`,
regenerate: with a scratch database, `uv run alembic revision
--autogenerate -m "…"` (pre-1.0 we *squash* instead of accumulating).

## Frontend

```bash
cd frontend
npm install
npm run dev        # Vite dev server, proxies /api to :8100
npm test           # vitest (jsdom)
npm run build      # type-check + production build
npm run gen:api    # regenerate src/api/schema.gen.ts from the backend
```

Never hand-write a type the backend defines — regenerate. The dev
server proxies `/api` and SSE to the backend.

## Playground

`deploy/playground` runs the full stack (app + disposable paperless
pre-seeded with a public-document corpus) for manual testing:

```bash
cd deploy/playground
cp .env.example .env    # point at your LLM
podman compose up -d
# app on :8100, paperless on :8210 (admin/admin)
```

The corpus consists of real, publicly shared documents; provenance is
tracked in `backend/tests/fixtures/corpus/external/MANIFEST.md`.

## Documentation

```bash
uvx --with mkdocs-material mkdocs serve   # live-preview this site
```

The site deploys to GitHub Pages automatically on pushes to `main`
(`.github/workflows/docs.yml`).

## Conventions

- Conventional commits; each commit leaves tests green.
- One error shape (`{detail: {code, message}}`), one pagination
  envelope, explicit response schema on every route.
- IDs never surface in the UI — names, titles, and labels do.
- Serving quirks (streaming support, image limits, thinking mode) are
  config, never hardcodes.
