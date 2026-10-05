import json
from time import monotonic

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db import transaction

from .presence import update_presence


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        if not self.user or not self.user.is_authenticated or not self.user.is_active:
            await self.close()
            return
        self.user_group = f"user_{self.user.id}"
        self._workflow_groups = (self.user_group, "workflow", "chat_public", "maintenance", "presence")
        await self._renew_group_memberships(force=True)
        await self.accept()
        await self._update_presence(force=True)

    async def _renew_group_memberships(self, *, force=False):
        # channels-redis expires group membership even while a socket remains
        # open. Authenticated heartbeats renew it well before the normal 24h
        # expiry, without five Redis writes on every ping.
        now = monotonic()
        interval = min(300, self.channel_layer.group_expiry / 2)
        last_renewal = getattr(self, "_last_group_renewal", None)
        if not force and last_renewal is not None and now - last_renewal < interval:
            return
        for group in self._workflow_groups:
            await self.channel_layer.group_add(group, self.channel_name)
        self._last_group_renewal = now

    async def _update_presence(self, *, connected=True, force=False):
        snapshot = await update_presence(
            self.channel_layer, self.user.id, self.channel_name, connected=connected,
        )
        if not force and not snapshot.changed:
            return
        await self.channel_layer.group_send(
            "presence",
            {
                "type": "user.presence",
                "user_id": self.user.id,
                "online": self.user.id in snapshot.user_ids,
                "online_user_ids": snapshot.user_ids,
                "revision": snapshot.revision,
            },
        )

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            for group in self._workflow_groups:
                await self.channel_layer.group_discard(group, self.channel_name)
            await self._update_presence(connected=False)

    async def receive(self, text_data):
        if not await self._refresh_user():
            await self.close(code=4001)
            return
        try:
            payload = json.loads(text_data or "{}")
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("type") == "ping":
            await self._renew_group_memberships()
            await self._update_presence(force=True)
            await self.send(text_data=json.dumps({"type": "pong"}))
            return
        if payload.get("type") == "chat_message":
            await self._create_message(payload)
            return
        if payload.get("type") in {"chat.typing", "chat.recording"}:
            typing = await self._typing_payload(payload)
            if typing:
                await self._broadcast_typing(typing)

    @database_sync_to_async
    def _refresh_user(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.filter(pk=self.user.pk, is_active=True).first()
        if user is None:
            return False
        self.user = user
        return True

    @database_sync_to_async
    @transaction.atomic
    def _create_message(self, payload):
        from design_workflow.models import ChatMessage, ChatThread, NotificationType
        from design_workflow.serializers import ChatMessageCreateSerializer
        from design_workflow.services import create_notification
        from design_workflow.views import (
            broadcast_chat_message, can_access_chat_thread, chat_thread_recipients,
            extract_chat_mentions, get_chat_message_queryset, sync_linked_thread_participants,
        )

        thread_id = payload.get("thread_id")
        body = payload.get("body") or payload.get("message") or ""
        if not isinstance(thread_id, int) or isinstance(thread_id, bool) or not isinstance(body, str) or not body.strip():
            return None
        serializer = ChatMessageCreateSerializer(data={
            "body": body,
            **({"reply_to_id": payload["reply_to_id"]} if "reply_to_id" in payload else {}),
        })
        if not serializer.is_valid():
            return None
        body = serializer.validated_data["body"].strip()
        try:
            thread = ChatThread.objects.prefetch_related("participants").get(pk=thread_id)
        except ChatThread.DoesNotExist:
            return None
        if not can_access_chat_thread(self.user, thread):
            return None
        reply_to = serializer.validated_data.get("reply_to")
        if reply_to and reply_to.thread_id != thread.id:
            return None
        sync_linked_thread_participants(thread, actor=self.user)
        message = ChatMessage.objects.create(thread=thread, sender=self.user, body=body, reply_to=reply_to)
        message.read_by.add(self.user)
        mentioned_users = extract_chat_mentions(body, thread, self.user)
        if mentioned_users:
            message.mentions.add(*mentioned_users)
        thread.save(update_fields=["updated_at"])
        for recipient in chat_thread_recipients(thread, self.user):
            create_notification(
                recipient=recipient,
                notification_type=NotificationType.CHAT_MESSAGE,
                payload={"thread_id": thread.id, "message_id": message.id, "title": self.user.first_name or self.user.email},
            )
        for recipient in mentioned_users:
            if recipient.id != self.user.id:
                create_notification(
                    recipient=recipient,
                    notification_type=NotificationType.CHAT_MESSAGE,
                    payload={"thread_id": thread.id, "message_id": message.id, "title": f"@ mention from {self.user.first_name or self.user.email}"},
                )
        # HTTP and socket sends now share serializers, mention rules,
        # notifications, and the same commit-only broadcast helper.
        message = get_chat_message_queryset().get(pk=message.pk)
        broadcast_chat_message(message)
        return message.pk

    @database_sync_to_async
    def _typing_payload(self, payload):
        from design_workflow.models import ChatThread
        from design_workflow.serializers import UserSummarySerializer
        from design_workflow.views import can_access_chat_thread, linked_thread_user_ids

        thread_id = payload.get("thread_id")
        if not isinstance(thread_id, int):
            return None
        try:
            thread = ChatThread.objects.prefetch_related("participants").get(pk=thread_id)
        except ChatThread.DoesNotExist:
            return None
        if not can_access_chat_thread(self.user, thread):
            return None
        return {
            "thread_kind": thread.kind,
            "thread_id": thread.id,
            "participant_ids": list(linked_thread_user_ids(thread)),
            "user": UserSummarySerializer(self.user).data,
            "is_typing": bool(payload.get("is_typing", True)),
            "is_recording": bool(payload.get("is_recording", True)),
            "event_type": "chat.recording" if payload.get("type") == "chat.recording" else "chat.typing",
        }

    async def _broadcast_typing(self, typing):
        event = {
            "type": typing.get("event_type", "chat.typing"),
            "thread_id": typing["thread_id"],
            "user": typing["user"],
            "is_typing": typing["is_typing"],
            "is_recording": typing.get("is_recording", False),
        }
        if typing["thread_kind"] == "public":
            await self.channel_layer.group_send("chat_public", event)
            return
        for user_id in typing["participant_ids"]:
            await self.channel_layer.group_send(f"user_{user_id}", event)

    async def chat_message(self, event):
        await self._send_chat_event(event, "chat_message")

    async def chat_read(self, event):
        await self._send_chat_event(event, "chat_read")

    async def chat_deleted(self, event):
        await self._send_chat_event(event, "chat_deleted")

    async def chat_updated(self, event):
        await self._send_chat_event(event, "chat_updated")

    async def chat_reaction(self, event):
        await self._send_chat_event(event, "chat_reaction")

    async def chat_decision(self, event):
        await self._send_chat_event(event, "chat_decision")

    async def chat_reminder(self, event):
        await self._send_chat_event(event, "chat_reminder")

    async def chat_typing(self, event):
        if event.get("user", {}).get("id") == self.user.id:
            return
        await self._send_chat_event(event, "chat_typing")

    async def chat_recording(self, event):
        if event.get("user", {}).get("id") != self.user.id:
            await self._send_chat_event(event, "chat_recording")

    @database_sync_to_async
    def _can_receive_chat_event(self, event):
        from design_workflow.models import ChatThread
        from design_workflow.views import can_access_chat_thread

        message = event.get("message")
        thread_id = event.get("thread_id") or (message.get("thread") if isinstance(message, dict) else None)
        if not isinstance(thread_id, int) or isinstance(thread_id, bool):
            return False
        thread = ChatThread.objects.filter(pk=thread_id).first()
        return bool(thread and can_access_chat_thread(self.user, thread))

    async def _send_chat_event(self, event, signal_type):
        if not await self._refresh_user():
            await self.close(code=4001)
            return
        if await self._can_receive_chat_event(event):
            await self.send(text_data=json.dumps({**event, "type": signal_type}))

    async def receive_group_message(self, event):
        if not await self._refresh_user():
            await self.close(code=4001)
            return
        await self.send(text_data=json.dumps(event))

    async def user_presence(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "message": {
                        "type": "USER_PRESENCE",
                        "user_id": event["user_id"],
                        "online": event["online"],
                        "online_user_ids": event["online_user_ids"],
                        "revision": event.get("revision"),
                    }
                }
            )
        )
