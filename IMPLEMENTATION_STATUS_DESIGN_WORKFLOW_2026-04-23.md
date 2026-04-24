# Design Workflow Implementation Status

Date: 2026-04-23

Reviewed against:

- `IMPLEMENTATION_PLAN_INTERNAL_DESIGN_CONTROL.md`
- `PLAN_FONCTIONNEL_INTERNAL_DESIGN_CONTROL.md`

## Done

- Local runtime ports aligned to requested values:
  - Frontend `3004`
  - Backend `8004`
- Frontend local/env/docker config updated for new ports:
  - `package.json`
  - `.env.local`
  - `.env.example`
  - `docker-compose.yml`
  - `Dockerfile`
  - `next.config.ts`
  - CORS test fixtures
- Backend local/env/docker config updated for new ports:
  - `.env`
  - `.env.example`
  - `docker-compose.yml`
  - `Dockerfile`
  - Django CORS defaults in `settings.py` and `settings_test.py`
- Workflow UI now covers core MVP screens from docs:
  - manager overview
  - board
  - my work
  - projects list
  - project detail
  - task detail
  - team workload
  - time reports
  - notifications
- Role routing/guards improved:
  - managers go to `/dashboard/overview`
  - designers go to `/dashboard/my-work`
  - designers are redirected away from manager-only overview/team/report pages
- Manager actions implemented in frontend:
  - create project
  - edit project
  - create task
  - edit full task details
  - reassign task with reason
- Designer/assignee actions implemented in frontend:
  - update own task status
  - add comments
  - log time
  - see shared task history
- Board interaction improved:
  - filters for search/project/status/priority/assignee
  - overdue/blocked toggles
  - drag and drop via `@dnd-kit`
  - move across columns and reorder within column
  - optimistic move with rollback on failure
- Notification UX improved:
  - unread badge in navigation
  - unread-only filter in notification center
  - mark-as-read action
- Reporting/workload UX improved:
  - workload summary visible on overview
  - date filters on time report page
- Project detail now exposes shared context required by docs:
  - task list
  - contributors
  - recent comments
  - recent activity history
- Backend scope already present before this pass and rechecked:
  - project/task/time/comment/activity/notification models
  - dashboard/projects/tasks/workload/report/notifications endpoints
  - reassignment with reason
  - websocket invalidation flow
  - due-soon/overdue notification task
- Backend project-detail coverage extended in this pass:
  - rollup serializers for comments/activity/contributors
  - project detail endpoint returns those rollups
  - regression tests added for manager and designer access

## Not Done Or Partial

- No new dedicated acceptance/E2E suite was added in this pass.
- Shared browser manual click-through was not completed from this terminal run.
  - Validation below is build/test/runtime-probe based.

## Verification

- Frontend dependency install completed with `bun install`
- Frontend production build passed with `bun run build`
- Frontend standalone TypeScript check passed with `bun x tsc --noEmit --pretty false`
- Backend syntax check passed with `python -m py_compile`
- Backend Django check passed with `python manage.py check`
  - note: shell environment had external `DEBUG=release`, so check was rerun with explicit local env vars
- Backend regression tests passed with `python -m pytest design_workflow\\tests.py -q`
- Internal-terminal runtime probe passed for backend:
  - `http://localhost:8004/api/health/`
- Internal-terminal backend route smoke passed:
  - `GET /api/design-workflow/dashboard/summary/` -> `401 Unauthorized`
  - `GET /api/design-workflow/projects/` -> `401 Unauthorized`
  - `GET /api/design-workflow/notifications/` -> `401 Unauthorized`
  - expected auth gate confirms routes exist and are mounted
- Internal-terminal runtime probe passed for frontend:
  - `http://localhost:3004/login`
- Internal-terminal frontend route smoke passed:
  - `/`
  - `/dashboard`
  - `/dashboard/board`
  - `/dashboard/projects`
  - `/dashboard/notifications`
