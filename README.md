# AI Scriptwriting Platform

Ticket 01 provides the first FastAPI service slice for account and session management.

## Local development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app.main:app --reload
```

Set `APP_DATABASE_URL` (or add it to `.env`) to use PostgreSQL. Without it, local development uses `sqlite:///./app.db`. An initial administrator can be created once through `POST /api/v1/admin/bootstrap`, or automatically at startup with `APP_BOOTSTRAP_ADMIN_USERNAME` and `APP_BOOTSTRAP_ADMIN_PASSWORD`.

## Compose

```bash
docker compose up --build
curl http://localhost:8000/healthz
```

The application runs `alembic upgrade head` before Uvicorn starts. The bootstrap password and setup secret should be supplied through environment configuration before exposing the service. For local HTTP-only development, set `APP_COOKIE_SECURE=false` explicitly.
