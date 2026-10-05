from django.apps import AppConfig


class AccountConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "account"
    verbose_name = "Comptes"
    label = "accounts"

    def ready(self):
        from . import signals  # noqa: F401
