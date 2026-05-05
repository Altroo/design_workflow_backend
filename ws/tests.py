import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from unittest.mock import AsyncMock, MagicMock

from design_workflow_backend.asgi import application
from ws.jwt_middleware import (
    SimpleJwtTokenAuthMiddleware,
    simplejwttokenauthmiddlewarestack,
)


@pytest.mark.asyncio
@pytest.mark.django_db
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
