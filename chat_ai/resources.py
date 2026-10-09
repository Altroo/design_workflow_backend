from dataclasses import dataclass
from design_workflow.models import Project, Task, ChatMessage
from design_workflow.serializers import ProjectWriteSerializer, TaskWriteSerializer
from design_workflow.views import ProjectDetailView, TaskDetailView


@dataclass(frozen=True)
class Resource:
    model: type
    editable: tuple = ()
    serializer: type | None = None
    view: type | None = None


RESOURCES = {
    "project": Resource(
        Project,
        ("name", "description", "priority", "start_date", "target_end_date"),
        ProjectWriteSerializer,
        ProjectDetailView,
    ),
    "task": Resource(
        Task,
        ("title", "description", "priority", "due_date", "blocked_reason"),
        TaskWriteSerializer,
        TaskDetailView,
    ),
    "message": Resource(ChatMessage),
}
