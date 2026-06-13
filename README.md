# Design Workflow Backend

Django REST API for a design operations platform for projects, tasks, workflow boards, assignments, team workload, comments, activity, attachments, chat, notifications, and realtime collaboration.

This is a production-oriented business backend. It models real operational workflows, authenticated staff access, API filtering, document/report generation, realtime notification plumbing, and testable domain behavior.

## What It Shows

- Backend ownership for a complete internal business application.
- Django REST API design across related business modules.
- PostgreSQL data modeling for operational records and audit/history needs.
- Auth, permissions, SSO subject handling, filters, dashboards, exports, and realtime events.
- Testable backend code with pytest tooling instead of only manual checks.

## Main Modules

- account
- design_workflow
- ws

## Key Capabilities

- Django REST API for design projects, tasks, comments, activity, attachments, workflow status, and users.
- Realtime collaboration support through Channels, Daphne, Redis, websocket routing, and chat-related endpoints.
- JWT/session auth, SSO subject support, permission-aware access, django-filter, and django-axes protection.
- Workflow data model designed for board views, assignment queues, review states, team workload, and task detail pages.
- pytest stack with async/cov/xdist support for API and websocket-adjacent behavior.

## Stack

- Python, Django 6, Django REST Framework
- PostgreSQL, django-filter, django-simple-history
- SimpleJWT, dj-rest-auth, django-axes, CORS
- Redis, Channels, channels-redis, Daphne, Celery-ready runtime
- Gunicorn, WhiteNoise, Pillow/OpenCV where media handling is needed
- pytest, pytest-django, pytest-cov, pytest-asyncio, pytest-xdist

## Related Repository

- Frontend: [Altroo/design_workflow_frontend](https://github.com/Altroo/design_workflow_frontend)

## Product Screenshots

Redacted production UI screens powered by this API. Sensitive names, amounts, dates, and records are blurred.

![Workflow board](docs/screenshots/design-workflow-board.png)

![Project list](docs/screenshots/design-workflow-projects.png)

## Local Setup

Create local-only environment variables for Django settings, database, Redis, media/static storage, CORS, and allowed hosts. Do not commit `.env` files or production credentials.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8004
```

On Windows, activate with `.venv\Scripts\activate`.

## Tests

```bash
python -m pytest
python -m pytest --cov
```

## Portfolio Note

The repository is public for portfolio review. Screenshots are redacted, and sensitive production values are intentionally hidden.
