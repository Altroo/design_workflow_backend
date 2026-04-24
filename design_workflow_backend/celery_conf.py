from __future__ import absolute_import, unicode_literals

from os import environ

from celery import Celery
from django.conf import settings

environ.setdefault("DJANGO_SETTINGS_MODULE", "design_workflow_backend.settings")

app = Celery("design_workflow_backend", broker=settings.CELERY_BROKER_URL)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.timezone = settings.TIME_ZONE
app.conf.setdefault("worker_cancel_long_running_tasks_on_connection_loss", True)
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]
app.autodiscover_tasks(
    packages=[
        "account.tasks",
        "design_workflow.tasks",
    ]
)
