from unittest.mock import Mock, patch

import pytest
from django.contrib.admin import AdminSite
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory

from .admin import CustomUserAdmin
from .models import CustomUser


@pytest.mark.parametrize(
    ("user_exists", "can_change", "error_type"),
    [(False, True, Http404), (True, False, PermissionDenied)],
)
def test_password_change_checks_access_before_form_or_email(
    user_exists, can_change, error_type
):
    model_admin = CustomUserAdmin(CustomUser, AdminSite())
    request = RequestFactory().post("/admin/accounts/customuser/123/password/")
    user = Mock(spec=CustomUser) if user_exists else None

    with (
        patch.object(model_admin, "get_object", return_value=user),
        patch.object(model_admin, "has_change_permission", return_value=can_change),
        patch.object(model_admin, "change_password_form") as password_form,
        patch.object(
            UserAdmin, "user_change_password", side_effect=error_type
        ) as parent,
        patch("account.admin.send_email.delay") as email,
        pytest.raises(error_type),
    ):
        # Keep the keyword used by Django's inherited admin URL.
        model_admin.user_change_password(request, id="123")

    parent.assert_called_once_with(request, "123", "")
    password_form.assert_not_called()
    email.assert_not_called()
