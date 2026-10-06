import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from channels_redis.core import RedisChannelLayer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from design_workflow.models import ChatMessage, ChatThread, Notification
from design_workflow.tests import make_designer
from design_workflow_backend import settings as production_settings
from design_workflow_backend.asgi import application
from ws.consumers import ChatConsumer
from ws.jwt_middleware import (
    SimpleJwtTokenAuthMiddleware,
    simplejwttokenauthmiddlewarestack,
)


def test_redis_socket_timeout_exceeds_channel_blocking_timeout():
    redis_host = production_settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"][0]

    assert redis_host["socket_timeout"] > RedisChannelLayer.brpop_timeout


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestWebSocketConsumer:
    async def async_setup(self):
        self.user_model = get_user_model()

        def _create_user_sync():
            return self.user_model.objects.create_user(
                email="wsuser@example.com", password="pass"
            )

        def _generate_token_sync(user_obj):
            return str(AccessToken.for_user(user_obj))

        create_user = database_sync_to_async(_create_user_sync)
        generate_token = database_sync_to_async(_generate_token_sync)

        self.user = await create_user()
        self.token = await generate_token(self.user)

    async def test_ping_message(self):
        await self.async_setup()

        communicator = WebsocketCommunicator(application, f"/ws?token={self.token}")
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"type": "ping"})
        for _ in range(3):
            response = await communicator.receive_json_from()
            if response.get("type") == "pong":
                break
        else:
            pytest.fail("WebSocket did not return a pong response.")

        await communicator.disconnect()

    async def test_invalid_token_sets_anonymous_user_and_rejects_connection(self):
        communicator = WebsocketCommunicator(application, "/ws?token=invalidtoken")
        connected, _ = await communicator.connect()
        assert not connected

    async def test_binary_frame_is_ignored_and_connection_stays_usable(self):
        await self.async_setup()
        communicator = WebsocketCommunicator(application, f"/ws?token={self.token}")
        connected, _ = await communicator.connect()
        assert connected
        try:
            await communicator.send_to(bytes_data=b"not a chat message")
            await communicator.send_json_to({"type": "ping"})
            for _ in range(3):
                response = await communicator.receive_json_from()
                if response.get("type") == "pong":
                    break
            else:
                pytest.fail("WebSocket stopped responding after a binary frame.")
        finally:
            await communicator.disconnect()

    async def test_missing_token_rejects_connection(self):
        communicator = WebsocketCommunicator(application, "/ws")
        connected, _ = await communicator.connect()
        assert not connected

    async def test_simplejwttokenauthmiddlewarestack_returns_middleware(self):
        # helper should wrap an inner app and return the middleware instance
        result = simplejwttokenauthmiddlewarestack(lambda scope, receive, send: None)
        assert callable(result)
        assert isinstance(result, SimpleJwtTokenAuthMiddleware)


class TestSimpleJwtTokenAuthMiddlewareExtra:
    """Tests for SimpleJwtTokenAuthMiddleware."""

    @pytest.mark.asyncio
    async def test_call_with_unicode_decode_error(self):
        """Test handling of malformed query string."""
        inner = AsyncMock()
        middleware = SimpleJwtTokenAuthMiddleware(inner)
        scope = {"type": "websocket", "query_string": b"\xff\xfe"}
        send = AsyncMock()

        await middleware(scope, AsyncMock(), send)

        send.assert_called()
        assert send.call_args[0][0]["type"] == "websocket.close"
        assert send.call_args[0][0]["code"] == 4001

    @pytest.mark.asyncio
    async def test_call_without_token(self):
        """Test handling of missing token."""
        inner = AsyncMock()
        middleware = SimpleJwtTokenAuthMiddleware(inner)
        scope = {"type": "websocket", "query_string": b""}
        send = AsyncMock()

        await middleware(scope, AsyncMock(), send)

        assert isinstance(scope["user"], AnonymousUser)
        send.assert_called()

    @pytest.mark.asyncio
    async def test_call_with_invalid_token(self):
        """Test handling of invalid token."""
        inner = AsyncMock()
        middleware = SimpleJwtTokenAuthMiddleware(inner)
        scope = {"type": "websocket", "query_string": b"token=invalid_jwt_token"}
        send = AsyncMock()

        await middleware(scope, AsyncMock(), send)

        send.assert_called()
        assert send.call_args[0][0]["type"] == "websocket.close"

    @pytest.mark.asyncio
    async def test_reject_connection_sends_close(self):
        """Test _reject_connection sends close message."""
        send = AsyncMock()
        await SimpleJwtTokenAuthMiddleware._reject_connection(send)
        send.assert_called_once_with({"type": "websocket.close", "code": 4001})


class TestSimpleJwtTokenAuthMiddlewareStackExtra:
    """Tests for simplejwttokenauthmiddlewarestack helper."""

    def test_returns_callable(self):
        """Test that helper returns a callable middleware."""
        result = simplejwttokenauthmiddlewarestack(MagicMock())
        assert callable(result)

    def test_wraps_inner_with_middleware(self):
        """Test that it wraps inner app with SimpleJwtTokenAuthMiddleware."""
        result = simplejwttokenauthmiddlewarestack(MagicMock())
        assert isinstance(result, SimpleJwtTokenAuthMiddleware)


async def _drain_socket(socket):
    while not await socket.receive_nothing(timeout=0.03):
        await socket.receive_json_from()


async def _receive_chat(socket):
    notifications = []
    for _ in range(12):
        event = await socket.receive_json_from()
        if event.get("message", {}).get("type") == "NOTIFICATION":
            notifications.append(event)
        if event.get("type") == "chat_message":
            return event["message"], notifications
    pytest.fail("Expected live chat message was not received")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind,expected_notifications", [("private", 2), ("public", 1)])
def test_raw_socket_sends_match_http_mentions_replies_and_notifications(
    kind, expected_notifications
):
    sender = make_designer("socket-author@example.test")
    peer = make_designer("socket-peer@example.test")
    thread = ChatThread.objects.create(kind=kind)
    thread.participants.add(sender, peer)
    reply = ChatMessage.objects.create(
        thread=thread, sender=peer, body="Source message"
    )
    payload = {"body": "Hello @socket-peer", "reply_to_id": reply.pk}
    tokens = [str(AccessToken.for_user(user)) for user in (sender, peer)]

    def snapshot(message_id):
        message = ChatMessage.objects.get(pk=message_id)
        notices = Notification.objects.filter(payload__message_id=message_id)
        return {
            "body": message.body,
            "mentions": list(message.mentions.values_list("pk", flat=True)),
            "read_by": list(message.read_by.values_list("pk", flat=True)),
            "reply_to": message.reply_to_id,
            "notifications": sorted(
                (notice.recipient_id, notice.type, notice.payload["title"])
                for notice in notices
            ),
        }

    def send_http():
        client = APIClient()
        client.force_authenticate(sender)
        response = client.post(
            f"/api/design-workflow/chat/threads/{thread.pk}/messages/",
            payload,
            format="json",
        )
        assert response.status_code == 201
        return response.data["id"]

    async def scenario():
        sockets = [
            WebsocketCommunicator(application, f"/ws?token={token}") for token in tokens
        ]
        try:
            for socket in sockets:
                assert (await socket.connect())[0]
            for socket in sockets:
                await _drain_socket(socket)
            await sockets[0].send_json_to(
                {"type": "chat_message", "thread_id": thread.pk, **payload}
            )
            outgoing, _ = await _receive_chat(sockets[0])
            incoming, notices = await _receive_chat(sockets[1])
            assert incoming["id"] == outgoing["id"]
            assert incoming["body"] == payload["body"]
            assert [user["id"] for user in incoming["mentions"]] == [peer.pk]
            assert incoming["reply_to"]["id"] == reply.pk
            assert len(notices) == expected_notifications
            socket_snapshot = await database_sync_to_async(snapshot)(incoming["id"])
            http_id = await database_sync_to_async(send_http)()
            assert socket_snapshot == await database_sync_to_async(snapshot)(http_id)
            assert socket_snapshot["read_by"] == [sender.pk]
        finally:
            for socket in sockets:
                await socket.disconnect()

    async_to_sync(scenario)()


@pytest.mark.django_db(transaction=True)
def test_raw_socket_message_rolls_back_when_notification_creation_fails():
    sender = make_designer("rollback-socket-sender@example.test")
    peer = make_designer("rollback-socket-peer@example.test")
    thread = ChatThread.objects.create(kind="private")
    thread.participants.add(sender, peer)
    consumer = ChatConsumer()
    consumer.user = sender
    with (
        patch(
            "design_workflow.services.create_notification",
            side_effect=RuntimeError("notification failed"),
        ),
        patch("design_workflow.views.broadcast_chat_message") as broadcast,
    ):
        with pytest.raises(RuntimeError, match="notification failed"):
            async_to_sync(consumer._create_message)(
                {"thread_id": thread.pk, "body": "Must roll back"}
            )
        broadcast.assert_not_called()
    assert not ChatMessage.objects.filter(thread=thread).exists()
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_raw_socket_rejects_replies_from_other_threads():
    sender = make_designer("reply-socket-sender@example.test")
    peer = make_designer("reply-socket-peer@example.test")
    thread = ChatThread.objects.create(kind="private")
    thread.participants.add(sender, peer)
    other = ChatThread.objects.create(kind="private")
    other.participants.add(peer)
    reply = ChatMessage.objects.create(thread=other, sender=peer, body="Private source")
    consumer = ChatConsumer()
    consumer.user = sender
    result = async_to_sync(consumer._create_message)(
        {"thread_id": thread.pk, "body": "Invalid reply", "reply_to_id": reply.pk}
    )
    assert result is None
    assert not ChatMessage.objects.filter(thread=thread).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "handler",
    [
        "chat_message",
        "chat_read",
        "chat_deleted",
        "chat_updated",
        "chat_reaction",
        "chat_decision",
        "chat_reminder",
        "chat_typing",
        "chat_recording",
    ],
)
def test_outbound_chat_rechecks_current_thread_access(handler):
    recipient = make_designer(f"{handler}-recipient@example.test")
    sender = make_designer(f"{handler}-sender@example.test")
    thread = ChatThread.objects.create(kind="private")
    thread.participants.add(recipient, sender)
    consumer = ChatConsumer()
    consumer.user = recipient
    consumer.send = AsyncMock()
    consumer.close = AsyncMock()
    event = {
        "type": handler.replace("_", ".", 1),
        "thread_id": thread.pk,
        "message": {"thread": thread.pk, "body": "Restricted"},
        "user": {"id": sender.pk},
    }
    async_to_sync(getattr(consumer, handler))(event)
    consumer.send.assert_awaited_once()
    consumer.send.reset_mock()
    thread.participants.remove(recipient)
    async_to_sync(getattr(consumer, handler))(event)
    consumer.send.assert_not_awaited()
    consumer.close.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("account_change", ["deactivate", "delete"])
def test_outbound_chat_closes_inactive_or_deleted_account(account_change):
    recipient = make_designer(f"{account_change}-recipient@example.test")
    thread = ChatThread.objects.create(kind="public")
    consumer = ChatConsumer()
    consumer.user = recipient
    consumer.send = AsyncMock()
    consumer.close = AsyncMock()
    if account_change == "deactivate":
        get_user_model().objects.filter(pk=recipient.pk).update(is_active=False)
    else:
        get_user_model().objects.filter(pk=recipient.pk).delete()
    async_to_sync(consumer.chat_message)(
        {"message": {"thread": thread.pk, "body": "Must not be delivered"}}
    )
    consumer.send.assert_not_awaited()
    consumer.close.assert_awaited_once_with(code=4001)


@pytest.mark.django_db(transaction=True)
def test_ping_publishes_fresh_presence_revision_even_without_membership_change():
    user = make_designer("presence-heartbeat@example.test")
    token = str(AccessToken.for_user(user))

    async def scenario():
        socket = WebsocketCommunicator(application, f"/ws?token={token}")
        try:
            assert (await socket.connect())[0]
            first = await socket.receive_json_from()
            assert first["message"]["type"] == "USER_PRESENCE"
            assert first["message"]["online_user_ids"] == [user.pk]
            await socket.send_json_to({"type": "ping"})
            messages = [
                await socket.receive_json_from(),
                await socket.receive_json_from(),
            ]
            assert any(event.get("type") == "pong" for event in messages)
            presence_event = next(
                event["message"]
                for event in messages
                if event.get("message", {}).get("type") == "USER_PRESENCE"
            )
            assert presence_event["online_user_ids"] == [user.pk]
            assert presence_event["revision"] >= first["message"]["revision"]
        finally:
            await socket.disconnect()

    async_to_sync(scenario)()


@pytest.mark.asyncio
async def test_authenticated_heartbeats_keep_every_group_live_past_original_expiry(
    monkeypatch,
):
    clock = [100.0]
    monkeypatch.setattr("ws.consumers.monotonic", lambda: clock[0])
    monkeypatch.setattr("channels.layers.time.time", lambda: clock[0])
    layer = InMemoryChannelLayer(group_expiry=4)
    consumer = ChatConsumer()
    consumer.channel_layer = layer
    consumer.channel_name = "heartbeat-renewal"
    consumer._workflow_groups = (
        "user_1",
        "workflow",
        "chat_public",
        "maintenance",
        "presence",
    )
    consumer._refresh_user = AsyncMock(return_value=True)
    consumer._update_presence = AsyncMock()
    consumer.send = AsyncMock()

    with patch.object(layer, "group_add", wraps=layer.group_add) as group_add:
        await consumer._renew_group_memberships(force=True)
        assert group_add.await_count == 5
        clock[0] = 101.0
        await consumer.receive('{"type":"ping"}')
        assert group_add.await_count == 5  # No Redis churn on every ping.
        clock[0] = 102.1
        await consumer.receive('{"type":"ping"}')
        assert group_add.await_count == 10
        clock[0] = 105.0  # The original t=100 membership has now expired.
        for group in consumer._workflow_groups:
            await layer.group_send(group, {"type": "test.live", "group": group})
            event = await asyncio.wait_for(
                layer.receive(consumer.channel_name), timeout=0.2
            )
            assert event["group"] == group


@pytest.mark.asyncio
async def test_group_renewal_is_throttled_to_five_minutes_with_default_expiry(
    monkeypatch,
):
    clock = [0.0]
    monkeypatch.setattr("ws.consumers.monotonic", lambda: clock[0])
    consumer = ChatConsumer()
    consumer.channel_layer = InMemoryChannelLayer()
    consumer.channel_name = "heartbeat-throttling"
    consumer._workflow_groups = (
        "user_1",
        "workflow",
        "chat_public",
        "maintenance",
        "presence",
    )
    with patch.object(
        consumer.channel_layer, "group_add", wraps=consumer.channel_layer.group_add
    ) as group_add:
        await consumer._renew_group_memberships(force=True)
        clock[0] = 299.0
        await consumer._renew_group_memberships()
        assert group_add.await_count == 5
        clock[0] = 300.0
        await consumer._renew_group_memberships()
        assert group_add.await_count == 10


@pytest.mark.asyncio
async def test_inactive_heartbeat_closes_before_renewing_membership():
    consumer = ChatConsumer()
    consumer._refresh_user = AsyncMock(return_value=False)
    consumer._renew_group_memberships = AsyncMock()
    consumer._update_presence = AsyncMock()
    consumer.close = AsyncMock()
    await consumer.receive('{"type":"ping"}')
    consumer.close.assert_awaited_once_with(code=4001)
    consumer._renew_group_memberships.assert_not_awaited()
    consumer._update_presence.assert_not_awaited()
