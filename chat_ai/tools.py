"""Bounded native reads and proposals. Never execute model-generated code or URLs."""

from datetime import date, timedelta
import time

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from chat_ai_assistant.contracts import (
    ChatAIError,
    ChatAITool,
    ChatAIToolRegistry,
    object_schema,
    ID,
    STRING,
)
from design_workflow.models import Project, Task, TaskStatus, ProjectStatus, ChatMessage
from design_workflow.permissions import can_manage_project, can_mutate_task
from design_workflow.views import get_chat_thread_queryset_for_user, TimeReportView
from .security import authorize, capabilities, is_manager
from .models import AuditEvent
from .navigation import ChatAINavigationResolver, ROUTES, DETAILS
from .resources import RESOURCES
from .labels import FIELD_LABELS
from .knowledge import ChatAIKnowledgeService

RESOURCE = {"type": "string", "enum": list(RESOURCES)}


def user_name(user):
    return f"{user.first_name} {user.last_name}".strip() or user.email


DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
FILTERS = {
    "project_name": STRING,
    "assignee_name": STRING,
    "mine": {"type": "boolean"},
    "status": STRING,
    "overdue": {"type": "boolean"},
    "archived": {"type": "boolean"},
}


def registry():
    specs = [
        (
            "search_records",
            "Search projects/tasks or permitted chat messages; all filters combine with AND.",
            object_schema(
                {
                    "resource": RESOURCE,
                    "query": STRING,
                    **FILTERS,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["resource"],
            ),
            ("read",),
        ),
        (
            "get_record",
            "Read a known record, or the current-page record.",
            object_schema({"resource": RESOURCE, "identifier": ID}, ["resource"]),
            ("read",),
        ),
        (
            "navigate",
            "Open an approved native page or a known project/task. Search names first.",
            object_schema(
                {
                    "resource": {
                        "type": "string",
                        "enum": list(ROUTES) + list(DETAILS),
                    },
                    "identifier": ID,
                },
                ["resource"],
            ),
            ("read",),
        ),
        (
            "knowledge",
            "Explain reviewed Design Workflow procedures.",
            object_schema({"query": STRING}, ["query"]),
            ("read",),
        ),
        (
            "previous_results",
            "Open an earlier result by one-based position, or list the previous results.",
            object_schema(
                {
                    "operation": {"type": "string", "enum": ["open", "list"]},
                    "index": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["operation"],
            ),
            ("read",),
        ),
        (
            "workload_summary",
            "Count non-archived tasks by their actual board status; supports project or my tasks.",
            object_schema({"project_name": STRING, "mine": {"type": "boolean"}}),
            ("read",),
        ),
        (
            "time_report",
            "Manager-only native recorded and live person-time. Includes every collaborator; one workday = 480 minutes.",
            object_schema({"project_name": STRING, "date_from": DATE, "date_to": DATE}),
            ("read", "report"),
        ),
        (
            "prepare_change",
            "PROPOSE a known project/task edit or archive; requires separate human confirmation. Never deletes.",
            object_schema(
                {
                    "resource": {"enum": ["project", "task"]},
                    "identifier": ID,
                    "operation": {"enum": ["update", "archive"]},
                    "changes": {
                        "type": "object",
                        "maxProperties": 5,
                        "propertyNames": {
                            "enum": sorted(
                                {f for r in RESOURCES.values() for f in r.editable}
                            )
                        },
                        "additionalProperties": {
                            "type": ["string", "null"],
                            "maxLength": 1000,
                        },
                    },
                },
                ["resource", "identifier", "operation"],
            ),
            ("read",),
        ),
    ]
    return ChatAIToolRegistry(
        [
            ChatAITool(
                name,
                description,
                schema,
                {"type": "object"},
                name,
                application="design_workflow",
                required_capabilities=caps,
                authorization="fresh native identity and per-record permissions",
                classification="proposal" if name == "prepare_change" else "read",
            )
            for name, description, schema, caps in specs
        ]
    )


class ChatAIToolExecutor:
    def __init__(
        self, user_id, company_id, request_id, state=None, context=None, audit=True
    ):
        self.user_id, self.company_id, self.request_id = user_id, company_id, request_id
        self.state, self.context, self.audit = state or {}, context or {}, audit

    def authorize(self):
        return authorize(self.user_id, self.company_id)

    authorize_context = authorize

    def capabilities(self):
        return capabilities(self.authorize())

    def output_labels(self):
        return FIELD_LABELS

    def authorize_knowledge(self, docs):
        if not ChatAIKnowledgeService.sources_authorized(
            [{"document_id": d["document_id"], "version": d["version"]} for d in docs],
            1,
            self.capabilities(),
        ):
            raise ChatAIError("CONTEXT_EXPIRED")

    def execute(self, name, args):
        started, outcome = time.monotonic(), "denied"
        try:
            tool = registry().validate(name, args)
            if not set(tool.required_capabilities) <= self.capabilities():
                raise ChatAIError("PERMISSION_DENIED")
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SET LOCAL statement_timeout = %s",
                            [tool.timeout_seconds * 1000],
                        )
                result = getattr(self, name)(**args)
                if time.monotonic() - started > tool.timeout_seconds:
                    raise ChatAIError("TOOL_TIMEOUT")
                self.authorize()
            outcome = "allowed"
            return result
        finally:
            if self.audit:
                AuditEvent.objects.create(
                    actor_id=self.user_id,
                    user_id=self.user_id,
                    company_id=1,
                    tool=name[:64],
                    outcome=outcome,
                    correlation_id=self.request_id,
                    model_version=settings.CHAT_AI_MODEL_ID,
                    duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                )

    def queryset(self, resource):
        user = self.authorize()
        # Native task/project GETs intentionally expose read-only cross-project cards.
        # No private message, label, attachment, activity or time history is projected.
        if resource == "project":
            return Project.objects.select_related("manager").prefetch_related(
                "collaborators"
            )
        if resource == "task":
            return Task.objects.select_related(
                "project", "current_assignee"
            ).prefetch_related("project__collaborators")
        if resource == "message":
            return ChatMessage.objects.filter(
                deleted_at__isnull=True,
                thread__in=get_chat_thread_queryset_for_user(user),
            ).select_related("sender", "thread")
        raise ChatAIError("INVALID_ARGUMENTS")

    def record(self, resource, identifier):
        if type(identifier) is not int or not 0 < identifier <= 2147483647:
            raise ChatAIError("INVALID_ARGUMENTS")
        obj = self.queryset(resource).filter(pk=identifier).first()
        if obj is None:
            raise ChatAIError("NOT_FOUND")
        return obj

    def serialize(self, resource, obj):
        user = self.authorize()
        if resource == "message":
            return {
                "id": obj.pk,
                "name": user_name(obj.sender),
                "description": obj.body[:400],
                "date": obj.created_at.isoformat(),
                "navigation": ChatAINavigationResolver.resolve("chat"),
            }
        writable = (
            can_manage_project(user, obj)
            if resource == "project"
            else can_mutate_task(user, obj)
        )
        archived = obj.archived or (resource == "task" and obj.project.archived)
        item = {
            "id": obj.pk,
            "name": obj.name if resource == "project" else obj.title,
            "description": obj.description[:400],
            "status": obj.status,
            "priority": obj.priority,
            "archived": obj.archived,
            "can_edit": bool(writable and not archived),
            "navigation": ChatAINavigationResolver.resolve(resource, 1, obj.pk),
        }
        if resource == "task":
            item["project"] = obj.project.name
            item["assignee"] = (
                user_name(obj.current_assignee) if obj.current_assignee else ""
            )
            item["date"] = obj.due_date.isoformat() if obj.due_date else None
        else:
            item["assignee"] = user_name(obj.manager)
            item["date"] = (
                obj.target_end_date.isoformat() if obj.target_end_date else None
            )
        return item

    def read_records(self, resource, ids):
        if (
            not isinstance(ids, list)
            or len(ids) > 10
            or any(type(i) is not int or i < 1 for i in ids)
        ):
            raise ChatAIError("INVALID_ARGUMENTS")
        objects = {obj.pk: obj for obj in self.queryset(resource).filter(pk__in=ids)}
        return [self.serialize(resource, objects[i]) for i in ids if i in objects]

    def filtered(
        self,
        resource,
        query="",
        project_name="",
        assignee_name="",
        mine=False,
        status="",
        overdue=False,
        archived=False,
    ):
        qs = self.queryset(resource)
        if resource == "message":
            if project_name or assignee_name or mine or status or overdue or archived:
                raise ChatAIError("INVALID_ARGUMENTS")
            return qs.filter(body__icontains=query) if query else qs
        qs = qs.filter(archived=archived)
        if resource == "task" and not archived:
            qs = qs.filter(project__archived=False)
        if query:
            field = "name" if resource == "project" else "title"
            qs = qs.filter(
                Q(**{field + "__icontains": query}) | Q(description__icontains=query)
            )
        if project_name:
            qs = qs.filter(
                **{
                    ("name" if resource == "project" else "project__name")
                    + "__icontains": project_name
                }
            )
        if assignee_name:
            prefix = "manager" if resource == "project" else "current_assignee"
            # Every part of a full name must match (first/last/email); no OR broadening.
            for part in assignee_name.split():
                qs = qs.filter(
                    Q(**{prefix + "__first_name__icontains": part})
                    | Q(**{prefix + "__last_name__icontains": part})
                    | Q(**{prefix + "__email__icontains": part})
                )
        if mine:
            owner = "manager_id" if resource == "project" else "project__manager_id"
            collab = (
                "collaborators__id"
                if resource == "project"
                else "project__collaborators__id"
            )
            match = Q(**{owner: self.user_id}) | Q(**{collab: self.user_id})
            if resource == "task":
                match |= Q(current_assignee_id=self.user_id)
            qs = qs.filter(match)
        if status:
            if status not in (
                ProjectStatus.values if resource == "project" else TaskStatus.values
            ):
                raise ChatAIError("INVALID_ARGUMENTS")
            qs = qs.filter(status=status)
        if overdue:
            if resource != "task":
                raise ChatAIError("INVALID_ARGUMENTS")
            qs = qs.filter(due_date__lt=timezone.localdate()).exclude(
                status=TaskStatus.DONE
            )
        return qs.distinct()

    def remember(self, resource, items):
        self.state = {
            "resource": resource,
            "ids": [x["id"] for x in items],
            "expires_at": (timezone.now() + timedelta(minutes=20)).isoformat(),
        }

    def search_records(self, resource, limit=10, **filters):
        found = list(
            self.filtered(resource, **filters).order_by("-created_at", "-pk")[
                : limit + 1
            ]
        )
        items = [self.serialize(resource, obj) for obj in found[:limit]]
        self.remember(resource, items)
        return {
            "type": "record_list",
            "resource": resource,
            "items": items,
            "has_more": len(found) > limit,
        }

    def get_record(self, resource, identifier=None):
        if identifier is None and self.context.get("resource") == resource:
            identifier = self.context.get("identifier")
        obj = self.record(resource, identifier)
        items = [self.serialize(resource, obj)]
        self.remember(resource, items)
        return {"type": "record_list", "resource": resource, "items": items}

    def navigate(self, resource, identifier=None):
        user = self.authorize()
        if resource in ("reports", "team", "overview") and not is_manager(user):
            raise ChatAIError("PERMISSION_DENIED")
        if resource in DETAILS:
            self.record(resource, identifier)
        return {
            "type": "navigation",
            "target": ChatAINavigationResolver.resolve(resource, 1, identifier),
        }

    def previous_results(self, operation, index=None):
        if (
            not self.state.get("ids")
            or self.state.get("expires_at", "") < timezone.now().isoformat()
        ):
            raise ChatAIError("CONTEXT_EXPIRED")
        items = self.read_records(self.state["resource"], self.state["ids"])
        if len(items) != len(self.state["ids"]):
            raise ChatAIError("CONTEXT_EXPIRED")
        if operation == "list":
            return {
                "type": "record_list",
                "resource": self.state["resource"],
                "items": items,
            }
        if index is None or index > len(items):
            raise ChatAIError("INVALID_ARGUMENTS")
        return {"type": "navigation", "target": items[index - 1]["navigation"]}

    def knowledge(self, query):
        return {
            "type": "knowledge",
            "documents": ChatAIKnowledgeService().retrieve(
                query, 1, self.capabilities()
            ),
        }

    def workload_summary(self, project_name="", mine=False):
        rows = (
            self.filtered("task", project_name=project_name, mine=mine)
            .order_by()
            .values("status")
            .annotate(count=Count("pk", distinct=True))
        )
        return {
            "type": "workload_summary",
            "counts": {row["status"]: row["count"] for row in rows},
            "project": project_name,
            "mine": mine,
        }

    def time_report(self, project_name="", date_from=None, date_to=None):
        user = self.authorize()
        if not is_manager(user):
            raise ChatAIError("PERMISSION_DENIED")
        params = {}
        if bool(date_from) != bool(date_to):
            raise ChatAIError("INVALID_ARGUMENTS")
        if date_from:
            try:
                start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
                if start > end:
                    raise ValueError()
            except ValueError:
                raise ChatAIError("INVALID_ARGUMENTS") from None
            params.update(start_date=date_from, end_date=date_to)
        if project_name:
            matches = list(Project.objects.filter(name__iexact=project_name)[:2])
            if len(matches) != 1:
                raise ChatAIError("MULTIPLE_MATCHES" if matches else "NOT_FOUND")
            params["project"] = matches[0].pk
        request = APIRequestFactory().get("/", params)
        force_authenticate(request, user=user)
        response = TimeReportView.as_view()(request)
        if response.status_code != 200:
            raise ChatAIError("ACTION_REJECTED")
        return {
            "type": "time_report",
            "minutes": sum(row["minutes"] for row in response.data),
            "project": project_name,
            "date_from": date_from,
            "date_to": date_to,
            "navigation": ChatAINavigationResolver.resolve("reports"),
        }

    def prepare_change(self, resource, identifier, operation, changes=None):
        from .actions import prepare

        return prepare(self, resource, identifier, operation, changes or {})
