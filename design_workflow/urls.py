from django.urls import path

from .views import (
    DashboardSummaryView,
    NotificationListView,
    NotificationReadView,
    ProjectDetailView,
    ProjectListCreateView,
    TaskCommentsView,
    TaskDetailView,
    TaskListCreateView,
    TaskReassignView,
    TaskStatusView,
    TaskTimeEntriesView,
    TimeReportView,
    WorkloadView,
)

app_name = "design_workflow"

urlpatterns = [
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("projects/", ProjectListCreateView.as_view(), name="projects"),
    path("projects/<int:pk>/", ProjectDetailView.as_view(), name="project-detail"),
    path("tasks/", TaskListCreateView.as_view(), name="tasks"),
    path("tasks/<int:pk>/", TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<int:pk>/status/", TaskStatusView.as_view(), name="task-status"),
    path("tasks/<int:pk>/reassign/", TaskReassignView.as_view(), name="task-reassign"),
    path("tasks/<int:pk>/comments/", TaskCommentsView.as_view(), name="task-comments"),
    path("tasks/<int:pk>/time-entries/", TaskTimeEntriesView.as_view(), name="task-time-entries"),
    path("workload/", WorkloadView.as_view(), name="workload"),
    path("reports/time/", TimeReportView.as_view(), name="time-report"),
    path("notifications/", NotificationListView.as_view(), name="notifications"),
    path("notifications/<int:pk>/read/", NotificationReadView.as_view(), name="notification-read"),
]
