from urllib.parse import parse_qs

from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import close_old_connections
from jwt import decode as jwt_decode
from jwt.exceptions import DecodeError
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import UntypedToken

from account.models import CustomUser


class SimpleJwtTokenAuthMiddleware(BaseMiddleware):
    """
    Simple JWT Token authorization middleware for Django Channels 3,
    ?token=<Token> querystring is reuired with the endpoint using this authentication
    middleware to work in synergy with Simple JWT
    """

    def __init__(self, inner):
        super().__init__(inner)
        self.inner = inner

    @database_sync_to_async
    def get_user_from_token(self, user_id):
        return CustomUser.objects.get(pk=user_id)

    async def __call__(self, scope, receive, send):
        # Close old database connections to prevent
        # usage of timed out connections
        close_old_connections()

        try:
            token = parse_qs(scope["query_string"].decode("utf8")).get("token", [None])[
                0
            ]
        except (UnicodeDecodeError, KeyError, IndexError, TypeError):
            token = None

        if not token:
            scope["user"] = AnonymousUser()
            await self._reject_connection(send)
            return None

        try:
            UntypedToken(token)  # type: ignore[arg-type]
        except (InvalidToken, TokenError):
            scope["user"] = AnonymousUser()
            await self._reject_connection(send)
            return None

        try:
            decoded_data = jwt_decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            scope["user"] = await self.get_user_from_token(decoded_data["user_id"])
        except (KeyError, CustomUser.DoesNotExist, DecodeError):
            scope["user"] = AnonymousUser()
            await self._reject_connection(send)
            return None

        return await super().__call__(scope, receive, send)

    @staticmethod
    async def _reject_connection(send):
        """Reject WebSocket connection by closing it immediately."""
        await send(
            {
                "type": "websocket.close",
                "code": 4001,
            }
        )


def simplejwttokenauthmiddlewarestack(inner):
    return SimpleJwtTokenAuthMiddleware(AuthMiddlewareStack(inner))
