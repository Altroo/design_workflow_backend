# Attachment uploads

Include `upload-limits.conf` inside the Design Workflow API's HTTPS `server`
block, replacing its previous `client_max_body_size 50M` directive. On the
shared production proxy the include is installed as
`/etc/nginx/conf.d/design-workflow-upload-limits.inc` so nginx does not also
load it at the global HTTP level.

The app accepts original attachments up to 10 GiB per file. Chat selections
totaling over 10 GiB must be sent in separate messages. The proxy permits
11 GiB to accommodate multipart overhead. Card
covers retain their separate 8 MiB limit and frontend image conversion.

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
