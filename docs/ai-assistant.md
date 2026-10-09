# Private writing assistant

This app reuses Contrat/Reservation's signed gateway contract and the existing
self-hosted AI services. It does not install a model or contact an external AI provider.

`POST /api/ai/assist/` is authenticated and returns a suggestion only. Supported
actions: `translate` (French/English), `fix_grammar`, `professionalize`.
The request limit is 5,000 characters and 10 requests/minute per user in a separate
`ai_assistant` throttle scope. No project, task, comment or message is saved by this
endpoint. Mentions and project/task reference tokens must survive unchanged.

## Enable during deployment

1. Register a **new dedicated** `design_workflow` secret in the shared gateway's
   `AI_ASSISTANT_SERVICE_KEYS` configuration, preserving all existing entries.
   Do not reuse another app's key. Reload the gateway application configuration
   using its existing deployment procedure; do not restart or duplicate model services.
2. Set the same secret in this backend's private environment:

   ```dotenv
   AI_ASSISTANT_ENABLED=True
   AI_ASSISTANT_SERVICE_NAME=design_workflow
   AI_ASSISTANT_SERVICE_SECRET=<dedicated-secret>
   AI_ASSISTANT_GATEWAY_URL=http://ai-assistant-gateway:8080
   AI_ASSISTANT_TIMEOUT_SECONDS=185
   ```

   Only the web service joins the existing external `ebh-ai-gateway` network.
   The model network and model endpoints stay private. Never expose the secret in
   frontend environment variables, build output or logs.
3. Enable `NEXT_PUBLIC_AI_ASSISTANT_ENABLED=true` before building the frontend.
   The flag is compiled into the app. Missing backend configuration returns a
   friendly unavailable error, never a fake suggestion.
4. Smoke-test all three actions using synthetic text and a normal designer account.
   Verify applying affects only the current draft, cancellation changes nothing,
   and manually saving/sending still follows the existing workflow permissions.

There is no schema migration. Publish the release/changelog and Maintenance version
only after the new frontend/backend and gateway integration are healthy.

## Local development

Leave the flags off until a private gateway URL and a dedicated registered key are
available. Local development may use an approved tunnel to the private gateway;
do not make the model publicly reachable. Tests use synthetic responses and need
neither an AI key nor network access:

```bash
.venv/bin/python -m pytest ai_assistant/tests.py -q --no-cov
```

The frontend covers project/task titles and descriptions, checklist text, cover
image descriptions, comments, chat writing/editing, review/version notes,
annotations, reminder notes and reassignment/blocking reasons. Numbers, dates,
selectors, account identities, filenames, labels and searches are not AI fields.
