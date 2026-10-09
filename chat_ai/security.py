"""Fresh native identity and per-record permissions; model output grants nothing."""

import hashlib
import re

from django.conf import settings
from account.models import CustomUser
from chat_ai_assistant.contracts import ChatAIError
from design_workflow.permissions import can_manage_project, can_mutate_task

SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._-]{16,}|"
    r"(?:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*\S+",
    re.I,
)


def validate_text(text):
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 4000
        or "\x00" in text
    ):
        raise ChatAIError("INVALID_ARGUMENTS")
    if SECRET.search(text):
        raise ChatAIError("SENSITIVE_INPUT")
    return text.strip()


def is_manager(user):
    return user.is_staff or user.is_superuser or user.role == "manager"


def authorize(user_id, company_id=1):
    if not settings.CHAT_AI_ASSISTANT_ENABLED:
        raise ChatAIError("APPLICATION_UNAVAILABLE")
    if type(company_id) is not int or company_id != 1:
        raise ChatAIError("PERMISSION_DENIED")
    user = CustomUser.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ChatAIError("NOT_AUTHENTICATED")
    return user


def capabilities(user):
    # Native workflow endpoints allow every active member to create projects.
    # Editing/archiving still requires an independent record-level check.
    return {"read", "create", "update", "archive"} | (
        {"report"} if is_manager(user) else set()
    )


def authorization_stamp(user_id, company_id=1):
    user = authorize(user_id, company_id)
    return hashlib.sha256(
        repr(
            (
                "design_workflow",
                user.pk,
                user.role,
                user.is_staff,
                user.is_superuser,
                sorted(capabilities(user)),
            )
        ).encode()
    ).hexdigest()


def authorize_change(user, resource, obj):
    allowed = (
        can_manage_project(user, obj)
        if resource == "project"
        else (can_mutate_task(user, obj) if resource == "task" else False)
    )
    if not allowed:
        raise ChatAIError("PERMISSION_DENIED")
    if obj.archived or (resource == "task" and obj.project.archived):
        raise ChatAIError("ACTION_REJECTED")
