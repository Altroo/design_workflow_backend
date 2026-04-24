from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.template.loader import render_to_string

from account.models import CustomUser
from .forms import CustomAuthShopChangeForm, CustomAuthShopCreationForm
from .tasks import send_email


class CustomUserAdmin(UserAdmin):
    add_form = CustomAuthShopCreationForm
    form = CustomAuthShopChangeForm
    model = CustomUser
    readonly_fields = ("date_updated",)
    list_display = (
        "id",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
        "date_joined",
        "date_updated",
    )
    list_filter = ("is_staff", "is_active")
    date_hierarchy = "date_joined"
    fieldsets = (
        (
            "Profile",
            {
                "fields": (
                    "email",
                    "password",
                    "first_name",
                    "last_name",
                    "gender",
                    "avatar",
                    "avatar_cropped",
                    "password_reset_code",
                    "default_password_set",
                )
            },
        ),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Date d'activité", {"fields": ("date_joined", "date_updated", "last_login")}),
    )
    add_fieldsets = (
        (
            "Profile",
            {
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "gender",
                )
            },
        ),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
    )
    search_fields = ("email",)
    ordering = ("-id",)

    def user_change_password(self, request, id, form_url=""):
        """Override the password change view to send an email with the new password."""
        user = self.get_object(request, id)
        if request.method == "POST":
            form = self.change_password_form(user, request.POST)
            if form.is_valid():
                new_password = form.cleaned_data.get("password1")
                form.save()
                message = render_to_string(
                    "new_password.html",
                    {
                        "first_name": user.first_name or user.email.split("@")[0],
                        "password": new_password,
                        "frontend_url": settings.FRONTEND_URL,
                    },
                )
                send_email.delay(
                    user_pk=user.pk,
                    email_=user.email,
                    mail_subject="Changement de mot de passe - E.B.H Réservation",
                    message=message,
                )
                return super().user_change_password(request, id, form_url)
        return super().user_change_password(request, id, form_url)


class HistoricalCustomUserAdmin(admin.ModelAdmin):
    """Read-only admin for viewing historical CustomUser records."""

    list_display = (
        "history_id",
        "id",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "history_type",
        "history_date",
        "history_user",
    )
    list_filter = (
        "history_type",
        "history_date",
        "is_active",
        "is_staff",
    )
    search_fields = (
        "email",
        "first_name",
        "last_name",
    )
    readonly_fields = [
        field.name
        for field in CustomUser._meta.get_fields()
        if hasattr(field, "name") and not field.many_to_many and not field.one_to_many
    ] + [
        "history_id",
        "history_date",
        "history_change_reason",
        "history_type",
        "history_user",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(CustomUser, CustomUserAdmin)

admin.site.register(CustomUser.history.model, HistoricalCustomUserAdmin)
