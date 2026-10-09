# Include inside the Design Workflow API HTTPS server block only.
location /api/chat-ai/ {
    proxy_pass http://host.docker.internal:8004;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 180s;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
}
