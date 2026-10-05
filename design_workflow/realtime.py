"""Commit workflow mutations before notifying connected clients."""
from django.db import transaction
from rest_framework.views import APIView

from .services import broadcast_workflow_event


class WorkflowAPIView(APIView):
    def dispatch(self, request, *args, **kwargs):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return super().dispatch(request, *args, **kwargs)
        with transaction.atomic():
            response = super().dispatch(request, *args, **kwargs)
            if response.status_code >= 400:
                transaction.set_rollback(True)
                return response
            resource = request.path.split("/design-workflow/", 1)[-1].split("/", 1)[0]
            if resource in {"projects", "labels", "views"}:
                # No private content/IDs are broadcast: clients refetch through
                # their normal permission-checked API queries.
                broadcast_workflow_event(resource)
            elif resource == "chat" and request.path.endswith("/chat/threads/"):
                from .models import ChatThread
                from .views import linked_thread_user_ids
                thread = ChatThread.objects.get(pk=response.data["id"])
                broadcast_workflow_event("chat", recipients=linked_thread_user_ids(thread))
            elif resource == "notifications":
                scope = "notification-preferences" if request.path.endswith("/preferences/") else "notifications"
                broadcast_workflow_event(scope, recipients=[request.user.id])
            return response
