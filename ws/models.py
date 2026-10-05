from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import models, transaction
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

MAINTENANCE_GROUP = "maintenance"


class WsMaintenanceState(models.Model):
    maintenance = models.BooleanField(default=False, verbose_name=_("Maintenance"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Maintenance")
        verbose_name_plural = _("Maintenance")


@receiver(post_save, sender=WsMaintenanceState)
@receiver(post_delete, sender=WsMaintenanceState)
def broadcast_maintenance_state(sender, instance, **kwargs):
    def publish():
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        latest = WsMaintenanceState.objects.order_by("-updated_at", "-pk").first()
        async_to_sync(channel_layer.group_send)(MAINTENANCE_GROUP, {
            "type": "receive_group_message",
            "message": {"type": "MAINTENANCE", "maintenance": bool(latest and latest.maintenance)},
        })
    transaction.on_commit(publish, robust=True)
