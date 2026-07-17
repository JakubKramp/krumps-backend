# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An async FastAPI "online cookbook" app. Users manage a fridge of products, recipes ("dishes") with
ingredients (auto-populated with nutritional data from an external API), and profiles with DRI
(Dietary Reference Intake) calculations scraped via Playwright. Runs on Postgres, with Celery
(broker: Redis) for scheduled background jobs, and Google Cloud Storage for dish images.

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

# Run tests with coverage (CI enforces --cov-fail-under=85 across the whole repo)
docker compose exec api pytest --cov=. --cov-report=term-missing -s

# Lint / type check (matches CI jobs `ruff-lint` and `type-check`)
uv run ruff check .
uv run ty check

# Migrations (Alembic)
docker compose exec api alembic revision --autogenerate -m "<MESSAGE>"
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1

# Playwright (needed for DRI scraping; also gated by the PLAYWRIGHT_ENABLED setting)
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
the new router into `app/main.py`'s `routers` list (prompted interactively, requires `APP_LOCATION`
to be set), and appends `{app_name}` to the coverage `source` list in `pyproject.toml`. See
`core/cli.py` and `core/constants.py` for the generated boilerplate.

## Architecture

The codebase is organized as domain modules, each following the same internal shape:
`models.py` (SQLAlchemy), `routes.py` (FastAPI `APIRouter`), `schemas.py` (Pydantic),
`constants.py` (optional), and `tests/`. The three domain modules are:

- **`auth/`** — `User` and `Profile` models, JWT login/register routes, DRI scraping
  (`auth/dri_scrapper.py`, Playwright-based against omnicalculator.com). DRI scraping runs as a
  FastAPI `BackgroundTasks` job on profile creation, only when `settings.PLAYWRIGHT_ENABLED` is true.
- **`recipes/`** — `Ingredient`, `Dish`, `IngredientItem`, `Product`, `Tag`, `Image`. Ingredient
  nutritional values are fetched from an external API on creation (`recipes/nutritional_data.py`,
  plain `aiohttp` REST calls — not Playwright). `Dish` also supports tags (many-to-many via
  `dish_tag`), favoriting by users (many-to-many via `user_dish`), and images uploaded to GCS with
  signed-URL access (one `Image` per dish may be flagged `is_main`, enforced by a partial unique
  index).
- **`fridge/`** — `Fridge` model (one per user, auto-created — see the SQLAlchemy `init` event
  listener on `User` in `auth/models.py`) and a Celery task (`fridge/tasks.py`) that marks expired
  `Product`s daily via `celery-beat` (`app/celery_app.py` defines the beat schedule).

Cross-module relationships are real FK relationships (e.g. `User.fridge`, `Fridge.products` →
`Product`, `Product.ingredient` → `Ingredient`), so model files import across module boundaries;
`TYPE_CHECKING`-only imports are used to avoid circular imports where needed.

Shared infrastructure lives outside the domain modules:

- **`app/`** — `main.py` assembles the `FastAPI` app, registers each module's router, and adds CORS
  middleware; `security.py` holds password hashing, JWT creation/verification, and the
  `get_current_user` / `get_current_user_optional` dependencies; `celery_app.py` configures the
  Celery app and beat schedule; `utils/db.py` defines the async engine, `Base`, and the
  `get_session` dependency used throughout for DB access.
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
  `pyproject.toml`); `*/tests/*`, `auth/dri_scrapper.py`, `recipes/nutritional_data.py`, and
  `core/` are omitted. `extract_data` (the actual Playwright automation in `dri_scrapper.py`) is
  always mocked out in tests, so changes to the scraped site's markup won't be caught by the suite.

## CI pipeline (`.github/workflows/ci.yml`)

`ruff-lint`, `type-check` (`ty`), and `bandit-check` run in parallel, then `tests` (via
`docker-compose-ci.yml`, requiring 85% coverage), then on `master` a build/push to Google Artifact
Registry and an SSH deploy to a VM (using `docker-compose-prod.yml`).

A second workflow, `.github/workflows/pr-linear-sync.yml`, runs on every push to a non-`master`
branch: it opens a draft PR to `master` the first time a branch is pushed (skipped if one already
exists), and syncs the Linear issue whose key is parsed out of the branch name (e.g. `JKR-119`) —
adding a comment and moving it to "In Review" the first time a PR is opened. It needs a
`LINEAR_API_KEY` repo secret and "Allow GitHub Actions to create and approve pull requests" enabled
in repo settings.
