import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return
        self.user_group = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.channel_layer.group_add("chat_public", self.channel_name)
        await self.channel_layer.group_add("maintenance", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)
            await self.channel_layer.group_discard("chat_public", self.channel_name)
            await self.channel_layer.group_discard("maintenance", self.channel_name)

    async def receive(self, text_data):
        payload = json.loads(text_data or "{}")
        if payload.get("type") == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))
            return
        if payload.get("type") == "chat_message":
            message = await self._create_message(payload)
            if message:
                await self._broadcast_message(message)

    @database_sync_to_async
    def _create_message(self, payload):
        from design_workflow.models import ChatMessage, ChatThread, ChatThreadKind
        from design_workflow.serializers import ChatMessageSerializer

        thread_id = payload.get("thread_id")
        body = (payload.get("body") or payload.get("message") or "").strip()
        if not thread_id or not body:
            return None
        try:
            thread = ChatThread.objects.prefetch_related("participants").get(pk=thread_id)
        except ChatThread.DoesNotExist:
            return None
        if thread.kind == ChatThreadKind.PRIVATE and not thread.participants.filter(id=self.user.id).exists():
            return None
        message = ChatMessage.objects.create(thread=thread, sender=self.user, body=body)
        message.read_by.add(self.user)
        thread.save(update_fields=["updated_at"])
        message = ChatMessage.objects.select_related("thread", "sender", "reply_to", "reply_to__sender").prefetch_related("attachments", "read_by", "thread__participants", "mentions").get(pk=message.pk)
        return {
            "thread_kind": message.thread.kind,
            "participant_ids": list(message.thread.participants.values_list("id", flat=True)),
            "data": ChatMessageSerializer(message).data,
        }

    async def _broadcast_message(self, message):
        event = {"type": "chat.message", "message": message["data"]}
        if message["thread_kind"] == "public":
            await self.channel_layer.group_send("chat_public", event)
            return
        for user_id in message["participant_ids"]:
            await self.channel_layer.group_send(f"user_{user_id}", event)

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({"type": "chat_message", "message": event["message"]}))

    async def chat_read(self, event):
        await self.send(text_data=json.dumps({"type": "chat_read", **event}))

    async def chat_deleted(self, event):
        await self.send(text_data=json.dumps({"type": "chat_deleted", **event}))

    async def receive_group_message(self, event):
        await self.send(text_data=json.dumps(event))
