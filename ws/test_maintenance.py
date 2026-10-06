from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from django.db import transaction
from django.utils import timezone

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
            "message": {"type": "MAINTENANCE", "maintenance": True},
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


def test_maintenance_missing_channel_layer_is_safe(django_capture_on_commit_callbacks):
    with (
        patch("ws.models.get_channel_layer", return_value=None),
        django_capture_on_commit_callbacks(execute=True),
    ):
        WsMaintenanceState.objects.create(maintenance=True)
    assert WsMaintenanceState.objects.get().maintenance is True
