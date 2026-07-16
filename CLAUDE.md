# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An async FastAPI "online cookbook" app. Users manage a fridge of products, recipes ("dishes") with
ingredients (auto-populated with nutritional data from an external API), and profiles with DRI
(Dietary Reference Intake) calculations scraped via Playwright. Runs on Postgres, with Celery
(broker: Redis) for scheduled background jobs.

## Commands

Everything runs through `docker compose`; there is no supported bare-metal workflow.

```sh
# Build and run the stack (api, postgres, redis, celery-worker, celery-beat)
docker compose up --build

# Run the full test suite
docker compose run api pytest .

# Run a single test file / test
docker compose run api pytest recipes/tests/test_recipes.py
docker compose run api pytest recipes/tests/test_recipes.py::test_name

# Run tests with coverage (this is what CI enforces, --cov-fail-under=85)
docker compose exec api pytest --cov=recipes --cov=auth --cov-report=term-missing -s

# Lint / type check (matches CI jobs `ruff-lint` and `type-check`)
uv run ruff check .
uv run ty check

# Migrations (Alembic)
docker compose exec api alembic revision --autogenerate -m "<MESSAGE>"
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1

# Playwright (needed for DRI scraping)
docker compose exec api playwright install
docker compose exec api playwright install-deps chromium
```

Environment config lives in `config/.env` (see `config/env.example` for required vars). CI copies
`config/env.example` to `config/.env` before running tests.

## Scaffolding a new module

A custom Typer CLI generates new domain modules:

```sh
python -m core.cli startapp {app_name}
```

This creates `{app_name}/{__init__.py, routes.py, schemas.py, models.py, tests/}`, optionally wires
the new router into `app/main.py`'s `routers` list, and adds `{app_name}` to the coverage `source`
list in `pyproject.toml`. See `core/cli.py` and `core/constants.py` for the generated boilerplate.

## Architecture

The codebase is organized as domain modules, each following the same internal shape:
`models.py` (SQLAlchemy/SQLModel), `routes.py` (FastAPI `APIRouter`), `schemas.py` (Pydantic),
`constants.py` (optional), and `tests/`. The three domain modules are:

- **`auth/`** — `User` and `Profile` models, JWT login/register routes, DRI scraping
  (`auth/dri_scrapper.py`, Playwright-based).
- **`recipes/`** — `Ingredient`, `Dish`, `IngredientItem`, `Product`, `Tag`, `Image`. Ingredient
  nutritional values are fetched from an external API on creation (`recipes/nutritional_data.py`).
- **`fridge/`** — `Fridge` model (one per user, auto-created — see the SQLAlchemy `init` event
  listener on `User` in `auth/models.py`) and a Celery task (`fridge/tasks.py`) that marks expired
  `Product`s daily via `celery-beat`.

Cross-module relationships are real FK relationships (e.g. `User.fridge`, `Fridge.products` →
`Product`, `Product.ingredient` → `Ingredient`), so model files import across module boundaries;
`TYPE_CHECKING`-only imports are used to avoid circular imports where needed.

Shared infrastructure lives outside the domain modules:

- **`app/`** — `main.py` assembles the `FastAPI` app and registers each module's router;
  `security.py` holds password hashing, JWT creation/verification, and the `get_current_user`
  dependency; `celery_app.py` configures the Celery app and beat schedule; `utils/db.py` defines
  the async engine, `Base`, and the `get_session` dependency used throughout for DB access.
- **`core/`** — the `startapp` CLI (`cli.py`, `constants.py`) and file upload abstraction
  (`files/file_upload_client.py`: `FileUploader` base class with `GcloudFileUploader` for
  production and `TestFileUploader` for tests, selected via `FILE_UPLOADER_CLASSES` based on the
  `CLOUD_PROVIDER` setting).
- **`config/settings.py`** — all environment-driven settings (DB URLs, JWT secret/algorithm,
  external API keys, Celery broker, GCloud storage, upload limits), loaded via `django-environ`.
- **`migrations/`** — Alembic migrations against the SQLAlchemy `Base` metadata.

All DB access is async (`AsyncSession`, `asyncpg`). Most relationships use `lazy="selectin"` to
avoid N+1s given the async context (no lazy-load-on-access outside a session).

## Testing

- `conftest.py` at the repo root provides the core async fixtures (`engine`, `db`, `client`,
  `session`); it creates/drops a real Postgres `test_db` per test rather than mocking the DB.
  Domain-level `conftest.py` files (e.g. `auth/tests/conftest.py`) add module-specific fixtures and
  are pulled in via `from auth.tests.conftest import *` in the root conftest.
- `pytest-asyncio` is configured with `asyncio_mode = "auto"` (see `pyproject.toml`), so async test
  functions don't need an explicit `@pytest.mark.asyncio` marker.
- Coverage source is limited to `fridge`, `recipes`, `auth` (see `[tool.coverage.run]` in
  `pyproject.toml`); `core/`, `*/tests/*`, `auth/dri_scrapper.py`, and `recipes/nutritional_data.py`
  are excluded.

## CI pipeline (`.github/workflows/ci.yml`)

`ruff-lint`, `type-check` (`ty`), and `bandit-check` run in parallel, then `tests` (via
`docker-compose-ci.yml`, requiring 85% coverage), then on `master` a build/push to Google Artifact
Registry and SSH deploy to a VM.
