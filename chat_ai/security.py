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

IDLE_MEME_ACCOUNTS = frozenset(
    {
        "mahmoud@casadilusso.ma",
        "khaoula@casadilusso.ma",
        "med.amine@casadilusso.ma",
        "maissam@casadilusso.ma",
        "maryam@casadilusso.ma",
    }
)


def idle_meme_enabled(user):
    return user.email.strip().casefold() in IDLE_MEME_ACCOUNTS


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


def is_admin(user):
    return user.is_staff or user.is_superuser


def authorize(user_id, company_id=1):
    if not settings.CHAT_AI_ASSISTANT_ENABLED:
        raise ChatAIError("APPLICATION_UNAVAILABLE")
    if type(company_id) is not int or company_id != 1:
        raise ChatAIError("PERMISSION_DENIED")
    user = CustomUser.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ChatAIError("NOT_AUTHENTICATED")
    if not is_admin(user) and not user.can_view:
        raise ChatAIError("PERMISSION_DENIED")
    return user


def capabilities(user):
    admin = is_admin(user)
    if not admin and not user.can_view:
        return set()
    caps = {"read"}
    for capability, flag in (
        ("create", "can_create"),
        ("update", "can_edit"),
        ("archive", "can_delete"),
    ):
        if admin or getattr(user, flag):
            caps.add(capability)
    if caps & {"update", "archive"}:
        caps.add("mutate")
    if admin:
        caps.add("report")
    return caps


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


def authorize_change(user, resource, obj, operation):
    if operation not in ("update", "archive") or operation not in capabilities(user):
        raise ChatAIError("PERMISSION_DENIED")
    allowed = (
        can_manage_project(user, obj)
        if resource == "project"
        else (can_mutate_task(user, obj) if resource == "task" else False)
    )
    if not allowed:
        raise ChatAIError("PERMISSION_DENIED")
    if obj.archived or (resource == "task" and obj.project.archived):
        raise ChatAIError("ACTION_REJECTED")
