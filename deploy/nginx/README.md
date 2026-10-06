# Attachment uploads

Include `upload-limits.conf` inside the Design Workflow API's HTTPS `server`
block, replacing its previous `client_max_body_size 50M` directive. On the
shared production proxy the include is installed as
`/etc/nginx/conf.d/design-workflow-upload-limits.inc` so nginx does not also
load it at the global HTTP level.

The app accepts original attachments up to 10 GiB per file. Chat selections
totaling over 10 GiB must be sent in separate messages. The proxy permits
11 GiB to accommodate multipart overhead. Card
covers retain their separate 8 MiB incoming limit. The browser reduces them
before upload; Django validates and stores only a WebP thumbnail (longest edge
960 px, at most 160 KiB), never the full-size card-image upload. Using an
attachment as a cover creates a separate thumbnail and preserves the attachment.

UUID thumbnail URLs under `/media/design_workflow/task_covers/` have private
browser caching for one year. Replacements always get new URLs. Keep the
proxy forwarding Django's `Cache-Control` and `Last-Modified` headers; the
frontend additionally uses Next.js Image optimization and its responsive-image
cache in production, as in Facturation. Development serves the thumbnail directly.

Existing covers need a one-time conversion after deploying the backend:

```sh
python manage.py optimize_card_images
python manage.py optimize_card_images --apply
```

The first command is a dry run. `--apply` permanently removes each old cover
only after its thumbnail is saved and the database transaction commits. It
does not touch attachments, invalid images, or files still used by another card.
Use `--task-id ID` to limit either command to one card. Re-running skips already
optimized images. No database schema migration is needed.

Validate with `nginx -t` before reloading nginx. Keep this proxy limit aligned
with `MAX_ATTACHMENT_UPLOAD_SIZE` and the frontend attachment limit.

Deploy the Uvicorn-based web container before enabling the 11 GiB proxy limit.
Both `Dockerfile` and `docker-compose.yml` use Uvicorn's `h11` HTTP transport and
`wsproto` WebSocket transport. Keep a single worker, as online presence currently
uses process-local state. Do not use Daphne for large uploads: it queues the
entire request in RAM before Django's disk-spilling handlers can consume it.
Uvicorn's HTTP read flow control bounds the incoming buffer while Django writes
the body to temporary storage. WebSocket chat uses the same ASGI application.

Keep nginx request buffering enabled: the proxy receives slow client uploads
before passing them upstream. `client_body_timeout` measures inactivity, not
overall upload duration. Allow enough temporary disk space on both the proxy
and application host for concurrent uploads (Django temporarily keeps the raw
request and parsed file). Attachment and history sizes use 64-bit database
columns so files above 2 GiB can be saved correctly.

Run `python -m pytest design_workflow_backend/test_upload_server.py` to check
HTTP backpressure and the deployment entrypoints. A real upload smoke check
must also verify the persisted file bytes, server memory, and WebSocket ping.
