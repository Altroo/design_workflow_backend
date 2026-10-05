import asyncio
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from channels.layers import InMemoryChannelLayer
from channels_redis.core import RedisChannelLayer

from ws import presence
from ws.presence import PRESENCE_LEASE_SECONDS, update_presence


@pytest.mark.asyncio
async def test_memory_leases_keep_user_online_until_last_tab_disconnects(monkeypatch):
    monkeypatch.setattr(presence, "monotonic", lambda: 1.0)
    layer = InMemoryChannelLayer()
    first = await update_presence(layer, 1, "tab-a")
    assert first.user_ids == [1]
    assert first.changed
    assert first.revision == 1_000_000
    second = await update_presence(layer, 1, "tab-b")
    assert second.user_ids == [1]
    assert not second.changed
    partial = await update_presence(layer, 1, "tab-a", connected=False)
    assert partial.user_ids == [1]
    assert not partial.changed
    last = await update_presence(layer, 1, "tab-b", connected=False)
    assert last.user_ids == []
    assert last.changed


@pytest.mark.asyncio
async def test_memory_renewal_expires_dead_connections_and_isolates_layers(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(presence, "monotonic", lambda: clock[0])
    layer = InMemoryChannelLayer()
    await update_presence(layer, 1, "live")
    await update_presence(layer, 2, "dead-worker")
    clock[0] += PRESENCE_LEASE_SECONDS - 1
    renewed = await update_presence(layer, 1, "live")
    assert renewed.user_ids == [1, 2]
    assert not renewed.changed
    clock[0] += 2
    expired = await update_presence(layer, 1, "live")
    assert expired.user_ids == [1]
    assert expired.changed
    assert expired.revision > renewed.revision
    isolated = await update_presence(InMemoryChannelLayer(), 3, "other-test")
    assert isolated.user_ids == [3]


@pytest.mark.asyncio
async def test_redis_failure_is_not_replaced_with_process_local_presence():
    layer = Mock(prefix="isolated-presence-test")
    layer.connection.return_value.eval = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    with pytest.raises(ConnectionError, match="Redis unavailable"):
        await update_presence(layer, 1, "worker-tab")
    layer.connection.assert_called_once_with(0)
    args = layer.connection.return_value.eval.call_args.args
    assert args[1:] == (1, "isolated-presence-test:design_workflow:presence:v1", "1:worker-tab", 1, 90)


@pytest.fixture
def isolated_redis_url():
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("redis-server is required for the real cross-worker Lua integration test")
    # Unix socket only, no TCP listener, persistence, or existing Redis access.
    # Keep its path short for macOS's Unix-domain socket length limit.
    with tempfile.TemporaryDirectory(prefix="dw-presence-", dir="/tmp" if Path("/tmp").is_dir() else tempfile.gettempdir()) as directory:
        socket = Path(directory) / "redis.sock"
        process = subprocess.Popen(
            [executable, "--port", "0", "--unixsocket", str(socket), "--save", "", "--appendonly", "no", "--dir", directory],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 5
            while not socket.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            if not socket.exists():
                output = process.stdout.read().decode() if process.poll() is not None else "startup timed out"
                pytest.fail(f"Isolated Redis did not start: {output}")
            yield f"unix://{socket}"
        finally:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.asyncio
async def test_real_redis_shares_worker_leases_renewal_expiry_and_revisions(isolated_redis_url):
    first_worker = RedisChannelLayer(hosts=[isolated_redis_url], prefix="presence-test")
    second_worker = RedisChannelLayer(hosts=[isolated_redis_url], prefix="presence-test")
    connection = first_worker.connection(0)
    key = "presence-test:design_workflow:presence:v1"
    try:
        first = await update_presence(first_worker, 1, "worker-a-tab")
        second = await update_presence(second_worker, 2, "worker-b-tab")
        assert first.user_ids == [1]
        assert second.user_ids == [1, 2]
        assert second.revision > first.revision
        # Delivery may be reordered between workers; the revision identifies
        # which complete snapshot is newer for frontend acceptance.
        assert sorted([second, first], key=lambda snapshot: snapshot.revision)[-1].user_ids == [1, 2]
        old_expiry = await connection.zscore(key, "1:worker-a-tab")
        await update_presence(second_worker, 1, "worker-b-second-tab")
        partial = await update_presence(first_worker, 1, "worker-a-tab", connected=False)
        assert partial.user_ids == [1, 2]
        assert not partial.changed
        renewed = await update_presence(second_worker, 1, "worker-b-second-tab")
        assert await connection.zscore(key, "1:worker-b-second-tab") > old_expiry
        assert 0 < await connection.ttl(key) <= PRESENCE_LEASE_SECONDS * 2
        # Simulate a dead worker's elapsed lease without a 90-second test wait.
        # This touches only our isolated process's exact owned test key.
        await connection.zadd(key, {"2:worker-b-tab": 0})
        await connection.set("unrelated-presence-test", "preserve")
        expired = await update_presence(first_worker, 1, "worker-b-second-tab")
        assert expired.user_ids == [1]
        assert expired.changed
        assert expired.revision > renewed.revision
        assert await connection.get("unrelated-presence-test") == b"preserve"
        assert await connection.zrange(key, 0, -1) == [b"1:worker-b-second-tab"]
        gone = await update_presence(second_worker, 1, "worker-b-second-tab", connected=False)
        assert gone.user_ids == []
        assert gone.changed
    finally:
        await first_worker.close_pools()
        await second_worker.close_pools()


@pytest.mark.asyncio
async def test_real_redis_concurrent_workers_do_not_lose_connections(isolated_redis_url):
    workers = [RedisChannelLayer(hosts=[isolated_redis_url], prefix="presence-concurrent-test") for _ in range(2)]
    try:
        await asyncio.gather(*[
            update_presence(workers[index % 2], index + 1, f"tab-{index}") for index in range(20)
        ])
        snapshot = await update_presence(workers[0], 1, "tab-0")
        assert snapshot.user_ids == list(range(1, 21))
        assert not snapshot.changed
    finally:
        for worker in workers:
            await worker.close_pools()
