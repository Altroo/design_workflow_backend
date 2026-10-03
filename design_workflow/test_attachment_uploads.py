import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from .models import ChatMessage, ChatMessageAttachment, ChatThread, Project, Task, TaskAttachment
from .tests import make_designer, make_manager


pytestmark = pytest.mark.django_db


@pytest.fixture
def attachment_context(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    manager = make_manager()
    designer = make_designer()
    project = Project.objects.create(name="Video project", manager=manager)
    task = Task.objects.create(project=project, title="6K render", current_assignee=designer, created_by=manager, updated_by=manager)
    client = APIClient()
    client.force_authenticate(designer)
    return client, task, designer, manager


def test_designer_large_attachment_preserves_original_bytes(attachment_context):
    client, task, _, _ = attachment_context
    # Above the old 50 MiB proxy cap, using real multipart parsing and disk storage.
    original = b"original-render\x00\xff" * (5 * 1024 * 1024)
    response = client.post(f"/api/design-workflow/tasks/{task.pk}/attachments/", {
        "file": SimpleUploadedFile("render.mp4", original, content_type="video/mp4"),
        "name": "Rendu final 6K",
    }, format="multipart")
    assert response.status_code == 201
    attachment = TaskAttachment.objects.get(pk=response.data["id"])
    assert attachment.size == len(original)
    assert attachment.mime_type == "video/mp4"
    with attachment.file.open("rb") as saved:
        assert hashlib.file_digest(saved, "sha256").digest() == hashlib.sha256(original).digest()


def test_task_rejects_file_above_limit_without_creating_attachment(attachment_context, settings):
    client, task, _, _ = attachment_context
    settings.MAX_ATTACHMENT_UPLOAD_SIZE = 4
    response = client.post(f"/api/design-workflow/tasks/{task.pk}/attachments/", {
        "file": SimpleUploadedFile("render.mp4", b"12345"), "name": "Final",
    }, format="multipart")
    assert response.status_code == 400
    assert not task.attachments.exists()


def test_task_accepts_file_exactly_at_limit(attachment_context, settings):
    client, task, _, _ = attachment_context
    settings.MAX_ATTACHMENT_UPLOAD_SIZE = 4
    response = client.post(f"/api/design-workflow/tasks/{task.pk}/attachments/", {
        "file": SimpleUploadedFile("render.mp4", b"1234"), "name": "Final",
    }, format="multipart")
    assert response.status_code == 201


def test_chat_rejects_oversized_batch_without_posting_message(attachment_context, settings):
    client, _, designer, manager = attachment_context
    thread = ChatThread.objects.create(kind="private")
    thread.participants.set([manager, designer])
    settings.MAX_ATTACHMENT_REQUEST_SIZE = 4
    response = client.post(f"/api/design-workflow/chat/threads/{thread.pk}/messages/", {
        "body": "Render", "files": [SimpleUploadedFile("a.mp4", b"123"), SimpleUploadedFile("b.mp4", b"123")],
    }, format="multipart")
    assert response.status_code == 400
    assert not thread.messages.exists()


def test_task_and_chat_persist_ten_gib_sizes(attachment_context, settings):
    _, task, designer, manager = attachment_context
    size = 10 * 1024 ** 3
    assert settings.MAX_ATTACHMENT_UPLOAD_SIZE == size
    attachment = TaskAttachment.objects.create(task=task, uploaded_by=designer, file="render.mp4", name="Render", size=size)
    thread = ChatThread.objects.create(kind="private")
    message = ChatMessage.objects.create(thread=thread, sender=designer)
    chat_attachment = ChatMessageAttachment.objects.create(message=message, file="render.mp4", name="Render", size=size)
    attachment.refresh_from_db()
    chat_attachment.refresh_from_db()
    assert attachment.size == chat_attachment.size == size
