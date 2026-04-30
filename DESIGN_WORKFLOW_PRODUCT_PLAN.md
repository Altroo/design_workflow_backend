# Design Workflow Product Readout

Date: 2026-04-27

## What this project does

Design Workflow is an authenticated project delivery app for design teams. It combines project management, Kanban execution, task collaboration, workload reporting, notifications, and real-time chat.

The backend is Django + Django REST Framework + Channels. It exposes API endpoints for projects, tasks, labels, checklist items, attachments, cover images, comments, time entries, workload, reports, notifications, and chat threads/messages. It stores workflow history through task activity records, creates notifications for assignments/status/comments/chat, and broadcasts task/chat changes over WebSockets.

The frontend is Next.js + React + Redux Toolkit Query. It provides manager and designer dashboards with role-based navigation. Managers can create projects/tasks, reassign work, inspect team load, view time reports, and manage the shared board. Designers can focus on assigned work, update task status, comment, upload files, and use chat.

Core product flow:

1. Manager creates a project with owner, date range, priority, and status.
2. Manager creates tasks inside project, adds assignees, due dates, labels, estimates, cover images, checklist items, and attachments.
3. Team moves task cards through Kanban statuses: backlog, todo, in progress, in review, blocked, done.
4. Status changes and reassignment create activity, time entries, notifications, and WebSocket updates.
5. Chat supports public/private threads, image/file attachments, replies, mentions, read tracking, and deletion.
6. Reports summarize workload and time spent per project.

## Current strengths

- Full vertical workflow exists: project -> task -> board -> activity -> notification.
- Good backend model coverage: projects, tasks, labels, attachments, checklist, comments, time, notifications, chat.
- Drag-and-drop board already updates status and sort order.
- Role-based access separates manager controls from designer work.
- Task cover image support already exists in backend and frontend.
- WebSocket layer already supports live task/chat events.

## Weak features found

- Board visuals were generic and did not use the stronger Lunacy kanban language.
- Kanban task cover images were flat rectangular blocks with little hierarchy.
- Card footers buried important signals instead of making people, time, checks, and files scannable.
- Empty cards without images had no strong visual identity.
- Board lanes used ordinary card styling instead of dedicated column containers.
- Filters take too much vertical weight compared with the board itself.
- Search field in global topbar is visual-only; it does not search workspace data.
- Notifications are visible but not deeply actionable beyond opening related task/chat.
- Time tracking is mostly automatic/passive; no timer, manual correction workflow, or designer timesheet view.
- Reporting is basic totals only; no velocity, cycle time, SLA, blocked time, review bottleneck, or forecast.
- No board templates, task templates, recurring work, or saved views.
- No design-review specific artifacts: approvals, versions, annotations, client feedback, asset handoff checklist.

## Design direction applied

The frontend board now follows the first shared Lunacy template: the desktop screen titled **Kanban Dashboard**.

- Pale slate workspace surface with white panels.
- Rounded column containers with compact colored header pills.
- White cards with soft layered shadows.
- Cover images now sit in polished rounded media wells with status chips overlaid.
- Card bottom metadata now emphasizes avatar stack, time, checklist progress, and attachments.
- Status colors now map to a cleaner future-work palette: indigo, amber, cyan, rose, emerald, slate.
- Hero shell now has a subtle grid and a thin signal stripe as the product signature.

Product signature proposal: **Signal Board**. The UI should feel like a calm command surface for design execution: white space, dense cards, crisp colored lane signals, soft shadows, and no decorative noise.

## Best plan found

### Phase 1 - Board visual upgrade

- Finish applying the Lunacy board language across all board states: loading, empty, drag overlay, task modal, mobile lanes.
- Add cover fallback art for cards without uploaded images: generated pattern from project/status/priority, not blank space.
- Add saved board density modes: compact, comfortable, visual.
- Make filters collapsible into one compact toolbar above lanes.
- Add proper workspace search using existing task/project/user endpoints.

### Phase 2 - Design-workflow features

- Add approval states: needs review, changes requested, approved.
- Add task version history for design artifacts and screenshots.
- Add annotation/comment pins on uploaded cover images or attachments.
- Add handoff checklist templates: source file, exported assets, specs, client approval, final delivery.
- Add reusable project/task templates for common design jobs.
- Add saved board views: My blockers, Due this week, Review queue, Client approval, Archived.

### Phase 3 - Intelligence and planning

- Add board insights panel: overdue risk, blocked age, review bottleneck, overloaded teammates.
- Add cycle-time and lead-time reports by project/status/assignee.
- Add capacity forecast from estimate minutes, due dates, and assignee load.
- Add notification batching and digest preferences.
- Add SLA rules: due soon, stale review, blocked too long, no assignee, no estimate.

### Phase 4 - Collaboration polish

- Link chat messages to tasks/projects.
- Add task-specific chat room or thread shortcut.
- Add @mention autocomplete in task comments, not only chat.
- Add notification actions: mark read, assign, move status, comment, snooze.
- Add attachment previews with image gallery and file metadata.

### Phase 5 - Production hardening

- Add API pagination for large task/project/chat lists.
- Add file validation: allowed mime types, max sizes, image dimensions.
- Add audit export for task activity.
- Add optimistic UI rollback coverage for all mutations.
- Add end-to-end tests for board drag, cover upload, comment, notification, and chat.

## Highest-value next build

Build **Signal Board v2**:

1. Compact filter toolbar.
2. Card cover fallback art.
3. Saved views.
4. Review approval workflow.
5. Workspace search wired to API.

This gives immediate visual difference and product depth without changing the whole architecture.
