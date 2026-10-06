"""Shared, expiring connection leases for cross-worker user presence."""

from dataclasses import dataclass
from time import monotonic
from weakref import WeakKeyDictionary

from channels.layers import InMemoryChannelLayer

PRESENCE_LEASE_SECONDS = 90
_memory_leases = WeakKeyDictionary()


@dataclass(frozen=True)
class PresenceSnapshot:
    user_ids: list[int]
    changed: bool
    revision: int


# One sorted set lives on the first configured channel-layer Redis host. Its
# members are individual user/channel connections, not process-local users.
# Redis TIME and a single script keep expiry/add/remove/snapshot atomic across
# workers. Only this application's namespaced key is ever pruned.
_UPDATE_LEASE = """
local key = KEYS[1]
local function users()
    local result, seen = {}, {}
    for _, member in ipairs(redis.call('ZRANGE', key, 0, -1)) do
        local user = string.match(member, '^(%d+):')
        if user and not seen[user] then
            seen[user] = true
            table.insert(result, user)
        end
    end
    table.sort(result)
    return result
end
local before = users()
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if ARGV[2] == '1' then
    redis.call('ZADD', key, now + tonumber(ARGV[3]), ARGV[1])
else
    redis.call('ZREM', key, ARGV[1])
end
local after = users()
if #after > 0 then redis.call('EXPIRE', key, tonumber(ARGV[3]) * 2) end
local changed = table.concat(before, ',') ~= table.concat(after, ',')
return {after, changed and 1 or 0, tonumber(clock[1]) * 1000000 + tonumber(clock[2])}
"""


async def update_presence(
    channel_layer, user_id: int, channel_name: str, *, connected: bool = True
) -> PresenceSnapshot:
    """Renew/remove one lease and return all live users plus membership change.

    The explicit InMemoryChannelLayer is the only local/test fallback. Redis
    failures must not silently substitute inaccurate per-process presence.
    """
    member = f"{user_id}:{channel_name}"
    if isinstance(channel_layer, InMemoryChannelLayer):
        leases = _memory_leases.setdefault(channel_layer, {})
        before = {int(key.split(":", 1)[0]) for key in leases}
        now = monotonic()
        for key in [key for key, expires in leases.items() if expires <= now]:
            leases.pop(key, None)
        if connected:
            leases[member] = now + PRESENCE_LEASE_SECONDS
        else:
            leases.pop(member, None)
        after = {int(key.split(":", 1)[0]) for key in leases}
        return PresenceSnapshot(sorted(after), before != after, int(now * 1_000_000))

    # Reuse channels-redis' configured connection/pool (including credentials,
    # TLS, database and timeouts) instead of inventing a separate Redis URL.
    connection = channel_layer.connection(0)
    key = f"{channel_layer.prefix}:design_workflow:presence:v1"
    user_ids, changed, revision = await connection.eval(
        _UPDATE_LEASE,
        1,
        key,
        member,
        int(connected),
        PRESENCE_LEASE_SECONDS,
    )
    return PresenceSnapshot(
        sorted(int(value) for value in user_ids), bool(changed), int(revision)
    )
