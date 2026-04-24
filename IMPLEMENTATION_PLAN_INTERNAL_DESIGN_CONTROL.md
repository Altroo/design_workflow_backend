# Internal Design Control V1 Implementation Plan

## Summary

- Build both target repos as greenfield apps by cloning the `base_backend` and `base_frontend` architecture, then rebrand and trim them for the new domain. This is the right starting point because `internal_design_control_backend` currently only contains the functional brief and `internal_design_control_frontend` has no usable scaffold yet.
- Target v1 is `MVP + Notifications`: projects, tasks, Kanban, comments, time logging, reassignment with traceability, manager dashboard, team workload, simple time reports, and an in-app notification center.
- Keep auth local for v1 by reusing the existing account stack, but formalize two business roles: `manager` and `designer`.
- Use selective realtime: standard CRUD stays API-driven; websocket events are added only for high-value refreshes such as task board updates, new comments, reassignment, and notifications.

## Implementation Changes

### Backend

- Scaffold `internal_design_control_backend` from `base_backend`, keeping Django 6, DRF, JWT auth, Channels, Celery, Redis, pytest, and the existing `account` + `ws` foundations.
- Rename the Django project package and environment values to the Internal Design Control identity, preserving the same deployment and local-dev conventions as the base template.
- Extend `account.CustomUser` with a `role` enum: `manager` or `designer`. Keep `is_staff` for Django admin access; default managers to `role=manager`, designers to `role=designer`.
- Add one business app, recommended name `design_control`, containing the core models:
  - `Project`: name, description, manager, start_date, target_end_date, priority, status, archived flag/timestamp.
  - `Task`: project, title, description, current_assignee, status, priority, due_date, estimated_minutes, actual_minutes cache, blocked_reason, sort_order, created_by, updated_by.
  - `TimeEntry`: task, user, minutes, work_date, note.
  - `TaskComment`: task, author, body.
  - `TaskActivity`: task, actor, action_type, metadata JSON, created_at. This is the single audit log for status changes, priority changes, due-date changes, reassignment, comments, and time entries.
  - `Notification`: recipient, type, task/project refs, payload JSON, read_at, created_at.
- Implement permission rules at the API layer:
  - Managers: full project/task CRUD, reassignment, priority/date changes, workload/report access, notification administration if needed.
  - Designers: read global board/project/task data, but may mutate only their own assigned tasks for `status`, `blocked_reason`, comments, and time entries.
- Keep the backend style aligned with the template: DRF `APIView` + serializers + filters rather than introducing a different API style.
- Add Celery jobs only where they create clear v1 value: due-soon and overdue notification generation on a schedule. Do not add email notifications in v1.

### Backend API and Realtime

- Add a new `/api/design-control/` namespace with these v1 endpoints:
  - `/dashboard/summary/`
  - `/projects/` and `/projects/<id>/`
  - `/tasks/` and `/tasks/<id>/`
  - `/tasks/<id>/status/`
  - `/tasks/<id>/reassign/`
  - `/tasks/<id>/comments/`
  - `/tasks/<id>/time-entries/`
  - `/workload/`
  - `/reports/time/`
  - `/notifications/`
  - `/notifications/<id>/read/`
- Standardize enums exposed by the API:
  - `UserRole`: `manager | designer`
  - `TaskStatus`: `backlog | todo | in_progress | in_review | blocked | done`
  - `Priority`: `low | medium | high | urgent`
  - `ProjectStatus`: `planned | active | on_hold | completed | archived`
- Use query params on list endpoints for the first release instead of bespoke search APIs: `project`, `assignee`, `status`, `priority`, `overdue`, `blocked`, `mine`, `start_date`, `end_date`.
- Extend the `ws` app to support user-scoped groups and two new payload families:
  - `TASK_EVENT`: created, updated, status_changed, reassigned, comment_added, time_logged.
  - `NOTIFICATION`: new notification, notification_read.
- On the frontend, websocket messages should invalidate/refetch API data instead of becoming the primary source of truth.

### Frontend

- Scaffold `internal_design_control_frontend` from `base_frontend`, keeping Next.js 16, App Router, NextAuth v5 beta, RTK Query, Redux-Saga, MUI 7, Sass, and Jest/RTL.
- Rebrand env vars, metadata, routes, assets, and auth copy for the Internal Design Control product.
- Extend auth/session/profile typing with `role`, and route users by role after login:
  - Managers land on `/dashboard/overview`
  - Designers land on `/dashboard/my-work`
- Build the v1 route set:
  - `/login`
  - `/dashboard/overview`
  - `/dashboard/board`
  - `/dashboard/my-work`
  - `/dashboard/projects`
  - `/dashboard/projects/[id]`
  - `/dashboard/tasks/[id]`
  - `/dashboard/team`
  - `/dashboard/reports/time`
  - `/dashboard/notifications`
- Keep the base provider/store structure, but add new RTK Query services and slices for `projects`, `tasks`, `dashboard`, `workload`, `reports`, and `notifications`.
- Reuse MUI/DataGrid for list-heavy screens; do not add a charting library in v1. Dashboard KPIs should use cards, badges, trend text, and compact tables.
- Add a Kanban board implementation using `@dnd-kit` for drag-and-drop. Dragging a card should optimistically update local column/order state, then call `/tasks/<id>/status/`, with rollback on failure.
- Adapt the navigation and page guards by role so designers do not see manager-only screens or actions.
- Implement task detail UX around a single source of truth:
  - Managers can edit all task fields.
  - Designers see the same task detail but only actionable controls for their assigned tasks.
  - Comments, time entries, and activity history are shown together on the task page.
- Add an in-app notification center with unread badge count, filtered list, and mark-as-read actions.

## Public Interfaces and Shared Types

- Backend serializers and frontend types must align on these core objects: `AuthenticatedUser`, `ProjectSummary`, `ProjectDetail`, `TaskCard`, `TaskDetail`, `TimeEntry`, `TaskComment`, `TaskActivity`, `NotificationItem`, `DashboardSummary`, `WorkloadRow`, and `TimeReportRow`.
- `TaskDetail` should include enough nested data for the task screen without extra round trips: project summary, current assignee, contributors, comments, recent activity, total logged minutes, and unread notification relevance if applicable.
- Reassignment requests should require a reason string in v1 so the activity log always explains why ownership changed.
- Time entries should use `minutes` as the stored unit across backend and frontend to avoid hour-format ambiguity.
- Notification preferences are not part of v1. All supported in-app notification types are enabled by default.

## Test Plan

- Backend model tests: project/task creation, task status transitions, actual-minute rollups, reassignment activity logging, comment/time-entry activity creation, notification creation, due-soon and overdue generation.
- Backend permission tests: manager full access, designer read-only global access, designer can mutate only assigned tasks, designer cannot reassign or edit manager-only fields.
- Backend API tests: dashboard summary aggregation, board filtering, workload aggregation, time report filtering, notification list/read flows.
- Backend websocket tests: task event broadcast, notification broadcast, and no broadcast leakage across users.
- Frontend auth/routing tests: role-based redirects, protected route enforcement, manager-only navigation visibility.
- Frontend service/store tests: RTK Query endpoints, websocket invalidation flow, optimistic board updates with rollback, unread notification badge updates.
- Frontend UI tests: Kanban drag/drop, task detail permissions, time logging form, comment creation, project detail summaries, workload page rendering, notification center mark-as-read.
- End-to-end acceptance scenarios:
  - Manager creates a project, adds tasks, assigns designers, and sees the board/dashboard update correctly.
  - Designer moves an assigned task to `in_review`, logs time, adds a blocking comment, and cannot change assignee or due date.
  - Manager reassigns a task with a reason and retains prior work history and time entries.
  - Overdue and due-soon tasks surface in dashboard, workload, and notifications consistently.

## Assumptions and Defaults

- The target repos should start from the base templates rather than from scratch line-by-line.
- Local accounts remain the only authentication mode in v1; SSO is deferred.
- Notifications in v1 are in-app only. Existing email infrastructure remains limited to account/auth flows.
- Advanced attachments, revision/version workflows, calendar/timeline views, and fine-grained capacity planning stay out of scope for the first release.
- The app serves a single internal design team in v1; no multi-tenant or multi-department partitioning is planned.
- Hard-delete flows for projects are not required in v1; project archival is the safer default once tasks/history exist.
