"""Explicit, single-use confirmation through the existing workflow endpoints."""

from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction, connection
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from simple_history.models import HistoricalRecords
from chat_ai_assistant.contracts import ChatAIError
from account.models import CustomUser
from design_workflow.models import Project
from design_workflow.views import TaskArchiveView
from .models import PendingAction, AuditEvent
from .resources import RESOURCES
from .security import authorize, authorize_change, validate_text


def fingerprint(resource, obj):
    values = {field: getattr(obj, field) for field in RESOURCES[resource].editable}
    values.update(updated_at=obj.updated_at, archived=obj.archived)
    project = obj if resource == "project" else obj.project
    values["project"] = [
        project.pk,
        project.manager_id,
        project.archived,
        list(project.collaborators.order_by("pk").values_list("pk", flat=True)),
    ]
    if resource == "project":
        values["tasks"] = list(
            obj.tasks.order_by("pk").values("pk", "updated_at", "archived", "status")
        )
    else:
        values["assignee"] = obj.current_assignee_id
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, cls=DjangoJSONEncoder).encode()
    ).hexdigest()


def validated_update(resource, obj, changes, user):
    spec = RESOURCES[resource]
    if not changes or set(changes) - set(spec.editable):
        raise ChatAIError("INVALID_ARGUMENTS")
    for value in changes.values():
        if value is not None:
            if not isinstance(value, str) or len(value) > 1000:
                raise ChatAIError("INVALID_ARGUMENTS")
            if value:
                validate_text(value)
    serializer = spec.serializer(
        obj,
        data=changes,
        partial=True,
        context={"request": type("Actor", (), {"user": user})()},
    )
    if not serializer.is_valid():
        raise ChatAIError("INVALID_ARGUMENTS")
    return changes


def prepare(executor, resource, identifier, operation, changes):
    if resource not in ("project", "task") or operation not in ("update", "archive"):
        raise ChatAIError("INVALID_ARGUMENTS")
    # Only a record actually selected/read in this conversation/current page is a target.
    known = (
        executor.state.get("resource") == resource
        and identifier in executor.state.get("ids", [])
        and executor.state.get("expires_at", "") > timezone.now().isoformat()
    )
    current = (
        executor.context.get("resource") == resource
        and executor.context.get("identifier") == identifier
    )
    if not (known or current):
        raise ChatAIError("CONTEXT_EXPIRED")
    obj = executor.record(resource, identifier)
    user = executor.authorize()
    authorize_change(user, resource, obj, operation)
    if operation == "update":
        validated_update(resource, obj, changes, user)
    elif changes:
        raise ChatAIError("INVALID_ARGUMENTS")
    if (
        PendingAction.objects.filter(
            user=user, consumed_at__isnull=True, expires_at__gt=timezone.now()
        ).count()
        >= 20
    ):
        raise ChatAIError("CONTEXT_LIMIT")
    action = PendingAction.objects.create(
        user=user,
        resource=resource,
        record_id=obj.pk,
        operation=operation,
        changes=changes,
        fingerprint=fingerprint(resource, obj),
        expires_at=timezone.now() + timedelta(minutes=5),
        instruction_id=executor.request_id,
    )
    return confirmation_card(action, obj)


def confirmation_card(action, obj):
    return {
        "type": "confirmation",
        "action_id": str(action.pk),
        "resource": action.resource,
        "record_id": obj.pk,
        "operation": action.operation,
        "label": obj.name if action.resource == "project" else obj.title,
        "changes": action.changes,
        "before": {
            key: str(getattr(obj, key)) if getattr(obj, key) is not None else None
            for key in action.changes
        },
        "running_tasks": obj.open_tasks_count if action.resource == "project" else 0,
        "expires_at": action.expires_at.isoformat(),
    }


def replay_confirmation(executor, descriptor):
    pending = PendingAction.objects.filter(
        pk=descriptor["confirmation_id"], user_id=executor.user_id
    ).first()
    if pending is None or pending.consumed_at or pending.expires_at <= timezone.now():
        return {
            "type": "confirmation_status",
            "status": "completed" if pending and pending.consumed_at else "expired",
        }
    obj = executor.record(pending.resource, pending.record_id)
    authorize_change(executor.authorize(), pending.resource, obj, pending.operation)
    if fingerprint(pending.resource, obj) != pending.fingerprint:
        raise ChatAIError("CONTEXT_EXPIRED")
    return confirmation_card(pending, obj)


@contextmanager
def native_history_request(request):
    missing = object()
    previous = getattr(HistoricalRecords.context, "request", missing)
    HistoricalRecords.context.request = request
    try:
        yield
    finally:
        if previous is missing:
            del HistoricalRecords.context.request
        else:
            HistoricalRecords.context.request = previous


@transaction.atomic
def confirm(request, id):
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = %s", [5000])
            cursor.execute("SET LOCAL statement_timeout = %s", [5000])
    action = (
        PendingAction.objects.select_for_update()
        .filter(pk=id, user=request.user)
        .first()
    )
    if action is None:
        raise ChatAIError("NOT_FOUND")
    if action.consumed_at or action.expires_at <= timezone.now():
        raise ChatAIError("CONTEXT_EXPIRED")
    spec = RESOURCES[action.resource]
    # Match native archive/create lock order: project, then card, then identity.
    project_id = (
        action.record_id
        if action.resource == "project"
        else spec.model.objects.filter(pk=action.record_id)
        .values_list("project_id", flat=True)
        .first()
    )
    project = Project.objects.select_for_update().filter(pk=project_id).first()
    if project is None:
        raise ChatAIError("CONTEXT_EXPIRED")
    obj = (
        project
        if action.resource == "project"
        else spec.model.objects.select_for_update().filter(pk=action.record_id).first()
    )
    if obj is None:
        raise ChatAIError("CONTEXT_EXPIRED")
    if action.resource == "project":
        list(obj.tasks.select_for_update().values_list("pk", flat=True))
    if fingerprint(action.resource, obj) != action.fingerprint:
        raise ChatAIError("CONTEXT_EXPIRED")
    if not CustomUser.objects.select_for_update().filter(pk=request.user.pk).exists():
        raise ChatAIError("NOT_AUTHENTICATED")
    user = authorize(request.user.pk)
    authorize_change(user, action.resource, obj, action.operation)
    if action.expires_at <= timezone.now():
        raise ChatAIError("CONTEXT_EXPIRED")
    factory = APIRequestFactory()
    if action.operation == "update":
        data = validated_update(action.resource, obj, action.changes, user)
        native = factory.patch("/", data, format="json")
        view = spec.view
    elif action.resource == "task":
        native = factory.post("/", {"archived": True}, format="json")
        view = TaskArchiveView
    else:
        native = factory.patch("/", {"archived": True}, format="json")
        view = spec.view
    force_authenticate(native, user=user)
    with native_history_request(request):
        response = view.as_view()(native, pk=obj.pk)
    if response.status_code >= 400:
        raise ChatAIError("ACTION_REJECTED")
    action.consumed_at = timezone.now()
    action.save(update_fields=["consumed_at"])
    AuditEvent.objects.create(
        user=user,
        actor_id=user.pk,
        actor_label=str(user)[:254],
        resource=action.resource,
        record_id=action.record_id,
        changed_fields=sorted(action.changes),
        company_id=1,
        tool="confirmed_" + action.operation,
        outcome="allowed",
        instruction_id=action.instruction_id,
        correlation_id=action.pk,
        model_version=settings.CHAT_AI_MODEL_ID,
    )
    return {
        "success": True,
        "operation": action.operation,
        "resource": action.resource,
        "record_id": action.record_id,
    }
