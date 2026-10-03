"""Exercise the deployed HTTP transport, not only DRF's in-process test client."""

import asyncio
import hashlib
import json
from pathlib import Path
import shlex
from unittest.mock import Mock

import pytest
from django.core.handlers.asgi import ASGIHandler
from uvicorn import Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.protocols.websockets.wsproto_impl import WSProtocol
from uvicorn.server import ServerState


@pytest.mark.asyncio
@pytest.mark.parametrize("size_mib", [64, 256])
async def test_large_requests_apply_backpressure_and_spill_to_disk(settings, size_mib):
    settings.FILE_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024
    chunk = b"original-video-bytes" * 3276  # A fixed, roughly 64 KiB network read.
    total_size = size_mib * 1024 * 1024
    expected = hashlib.sha256()
    received = {}

    async def app(scope, receive, send):
        body = await ASGIHandler().read_body(receive)
        try:
            received["spilled"] = body._rolled
            received["size"] = 0
            digest = hashlib.sha256()
            while data := body.read(64 * 1024):
                received["size"] += len(data)
                digest.update(data)
            received["digest"] = digest.digest()
        finally:
            body.close()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    config = Config(app, http="h11", ws="wsproto", lifespan="off", access_log=False)
    config.load()
    assert config.ws_protocol_class is WSProtocol
    server_state = ServerState()
    protocol = H11Protocol(config, server_state, {})
    transport = Mock()
    transport.is_closing.return_value = False
    transport.get_extra_info.side_effect = lambda key: ("127.0.0.1", 8004) if key in ("sockname", "peername") else None
    protocol.connection_made(transport)
    max_buffer = 0
    try:
        protocol.data_received(f"POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: {total_size}\r\n\r\n".encode())
        remaining = total_size
        while remaining:
            # A real asyncio transport stops delivering data while paused.
            while protocol.flow.read_paused:
                await asyncio.sleep(0)
            data = chunk[:min(len(chunk), remaining)]
            expected.update(data)
            protocol.data_received(data)
            max_buffer = max(max_buffer, len(protocol.cycle.body))
            remaining -= len(data)
        await asyncio.wait_for(asyncio.gather(*server_state.tasks), timeout=10)
        assert received == {"spilled": True, "size": total_size, "digest": expected.digest()}
        assert max_buffer <= 128 * 1024
        assert transport.pause_reading.call_count > 0
        assert transport.resume_reading.call_count > 0
    finally:
        protocol.connection_lost(None)


def test_container_entrypoints_use_the_backpressured_transport():
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text()
    command = next(line.removeprefix("CMD ") for line in dockerfile.splitlines() if line.startswith("CMD "))
    compose = (root / "docker-compose.yml").read_text()
    web = compose.split("  web:\n", 1)[1].split("  db:\n", 1)[0]
    compose_command = next(line.strip().removeprefix("command: ") for line in web.splitlines() if line.strip().startswith("command: "))
    for args in (json.loads(command), shlex.split(compose_command)):
        assert args[:2] == ["uvicorn", "design_workflow_backend.asgi:application"]
        for option, value in (("--http", "h11"), ("--ws", "wsproto"), ("--lifespan", "off")):
            assert args[args.index(option) + 1] == value
