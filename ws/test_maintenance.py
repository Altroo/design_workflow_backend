from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient

from .models import MAINTENANCE_GROUP, WsMaintenanceState

pytestmark = pytest.mark.django_db


@pytest.fixture(name="sender")
def sender_fixture():
    with patch("ws.models.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        yield layer.return_value.group_send


def test_maintenance_save_broadcasts_after_commit(
    sender, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        WsMaintenanceState.objects.create(maintenance=True)
        sender.assert_not_called()
    sender.assert_awaited_once_with(
        MAINTENANCE_GROUP,
        {
            "type": "receive_group_message",
            "message": {"type": "MAINTENANCE", "maintenance": True, "version": "0.1.0"},
        },
    )


def test_maintenance_rollback_does_not_publish(
    sender, django_capture_on_commit_callbacks
):
    with (
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(ValueError),
        transaction.atomic(),
    ):
        WsMaintenanceState.objects.create(maintenance=True)
        raise ValueError("rollback")
    sender.assert_not_called()
    assert not WsMaintenanceState.objects.exists()


def test_maintenance_callbacks_publish_final_committed_state_not_stale_instances(
    sender, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        state = WsMaintenanceState.objects.create(maintenance=True)
        state.maintenance = False
        state.save()
        sender.assert_not_called()
    assert sender.await_count == 2
    assert all(
        call.args[1]["message"]["maintenance"] is False
        for call in sender.await_args_list
    )


def test_deleting_latest_maintenance_restores_previous_state(
    sender, django_capture_on_commit_callbacks
):
    previous = WsMaintenanceState.objects.create(maintenance=False)
    WsMaintenanceState.objects.filter(pk=previous.pk).update(
        updated_at=timezone.now() - timedelta(minutes=5)
    )
    latest = WsMaintenanceState.objects.create(maintenance=True)
    with django_capture_on_commit_callbacks(execute=True):
        latest.delete()
        sender.assert_not_called()
    assert sender.await_args.args[1]["message"] == {
        "type": "MAINTENANCE",
        "maintenance": False,
        "version": "0.1.0",
    }


def test_deleting_last_maintenance_record_disables_maintenance(
    sender, django_capture_on_commit_callbacks
):
    state = WsMaintenanceState.objects.create(maintenance=True)
    with django_capture_on_commit_callbacks(execute=True):
        state.delete()
        sender.assert_not_called()
    sender.assert_awaited_once()
    assert sender.await_args.args[1]["message"]["maintenance"] is False


def test_maintenance_delete_rollback_preserves_state_and_emits_nothing(
    sender, django_capture_on_commit_callbacks
):
    state = WsMaintenanceState.objects.create(maintenance=True)
    with (
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(ValueError),
        transaction.atomic(),
    ):
        state.delete()
        raise ValueError("rollback")
    sender.assert_not_called()
    assert WsMaintenanceState.objects.get().maintenance is True


def test_public_bootstrap_returns_default_version_without_caching():
    response = APIClient().get("/api/ws/maintenance/")
    assert response.status_code == 200
    assert response.data == {"maintenance": False, "version": "0.1.0"}
    assert response["Cache-Control"] == "no-store"


def test_bootstrap_and_broadcast_use_same_latest_record(
    sender, django_capture_on_commit_callbacks
):
    first = WsMaintenanceState.objects.create(maintenance=True, version="1.0.0")
    with django_capture_on_commit_callbacks(execute=True):
        latest = WsMaintenanceState.objects.create(maintenance=False, version="1.2.0")
    # Tied timestamps must not choose a different row for HTTP than for WebSocket.
    WsMaintenanceState.objects.filter(pk=first.pk).update(updated_at=latest.updated_at)
    data = APIClient().get("/api/ws/maintenance/").data
    assert data == {"maintenance": False, "version": "1.2.0"}
    assert sender.await_args.args[1]["message"] == {"type": "MAINTENANCE", **data}


def test_version_only_change_broadcasts_after_commit(
    sender, django_capture_on_commit_callbacks
):
    state = WsMaintenanceState.objects.create(version="1.0.0")
    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        state.version = "1.10.0"
        state.save()
        sender.assert_not_called()
    assert sender.await_args.args[1]["message"] == {
        "type": "MAINTENANCE",
        "maintenance": False,
        "version": "1.10.0",
    }


@pytest.mark.parametrize(
    "version",
    ["", "1", "1.0", "01.0.0", "1.0.0-beta", "-1.0.0", "1.0.0\n", "1000000.0.0"],
)
def test_invalid_release_version_rejected(version):
    with pytest.raises(ValidationError):
        WsMaintenanceState(version=version).full_clean()


@pytest.mark.parametrize("version", ["0.0.0", "1.10.2", "2026.10.6"])
def test_valid_release_version(version):
    WsMaintenanceState(version=version).full_clean()


def test_public_bootstrap_cannot_publish_a_version():
    assert (
        APIClient().post("/api/ws/maintenance/", {"version": "99.0.0"}).status_code
        == 405
    )
    assert not WsMaintenanceState.objects.exists()


def test_maintenance_missing_channel_layer_is_safe(django_capture_on_commit_callbacks):
    with (
        patch("ws.models.get_channel_layer", return_value=None),
        django_capture_on_commit_callbacks(execute=True),
    ):
        WsMaintenanceState.objects.create(maintenance=True)
    assert WsMaintenanceState.objects.get().maintenance is True
