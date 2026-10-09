# Conversational assistant — Design Workflow

This is separate from `/api/ai/assist/` and the three writing buttons. It uses the
same private `chat-ai-assistant` engine as Facturation/Management Projet, with
Design Workflow tools, native permissions and bilingual reviewed help documents.

## Features and boundaries

- French/English questions, suggested starters and optional shortcuts.
- Project/task search and private-chat-aware message search, at most 10 results.
- Native-page links, follow-up references and current-card context.
- Task counts; manager-only recorded/live person-time from `TimeReportView`.
  One displayed workday equals 480 minutes. No new time calculation engine.
- Conversation history (30 days), refreshed results, cancellation and feedback.
- Proposed title/name, description, priority and date edits, plus archiving.
  Every write needs the explicit confirmation button. Previews expire after five
  minutes; native permissions, record fingerprints and ownership are rechecked.
  Native views retain activity, notifications, WebSocket and work-session effects.
- Projects/tasks stay readable across the workspace, matching native GETs.
  Only permitted owners/collaborators/assignees can change them. Managers cannot
  read somebody else's private chat merely because they are managers.
- No automatic approval, reassignment, membership edits, bulk writes or deletion.
  Those workflows remain in the existing card/project UI.
- The model receives bounded user prompts and typed context, not complete chat
  histories, attachments, time records or a database dump. Business results are
  structured backend cards, not invented model prose. No hosted-model fallback.

## Setup and deployment

1. Install `requirements.txt` (includes the pinned wheel in `vendor/`). The wheel
   was reused unchanged from the Management Projet integration:
   `a93f3975d05376ae6a3f2a9e954e2df317c28dfa880976da83e1720a2e71419d` (SHA-256).
2. Set the `CHAT_AI_*` variables in a private runtime environment. The model URL
   must resolve to the existing internal model service; reuse its dedicated
   inference key and immutable model ID. Never expose the key to the frontend.
   Do not create another model container or alter the writing gateway.
3. Run `python manage.py migrate` and `python manage.py sync_ai_knowledge`.
4. Enable `CHAT_AI_ASSISTANT_ENABLED=True` and build/run the frontend with
   `NEXT_PUBLIC_CHAT_AI_ASSISTANT_ENABLED=true`. Both default to disabled.
5. Production Compose joins only the web service to the existing external
   `chat-ai-facturation-internal` network. Use its unique `chat-ai-model` alias;
   never use the shared `web` alias. Do not start or rebuild the shared model.
   The SSE endpoint sends `X-Accel-Buffering: no`; keep proxy response buffering
   disabled for it and allow 180 seconds for a streamed response. Install
   `deploy/nginx/chat-ai.inc` in the shared proxy's `conf.d` directory and include
   it only inside the Design Workflow API HTTPS server block. Validate nginx
   before reloading; no other application's block needs to change.
6. Draft bilingual notes are in `docs/assistant-release-notes.json`. Version
   numbering, changelog publication and the Maintenance version announcement
   are coordinated separately with the agent handling the app-update feature.
   This feature deployment does not change or publish an app version.

`chat_ai.purge_history` runs daily via the existing Celery Beat schedule. It
removes expired conversations and proposals but retains confirmed-write audits.
Audit rows do not retain raw prompts or private tool-result snapshots.

## Verification

Run `pytest chat_ai --no-cov` for authorization, filtering, native confirmations,
archive effects, history freshness, knowledge, streaming and retention tests.
Frontend tests cover the panel, navigation allowlist, stream parser, confirmation
dialog and searchable project control. Live-model browser checks must be reported
separately from mocked tests: small-model behavior is not broad accuracy certification.
