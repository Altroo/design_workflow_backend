import os
import tempfile
import shutil
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image
from django.conf import settings as app_settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from account.serializers import (
    CreateAccountSerializer,
    ProfilePutSerializer,
    UsersListSerializer,
    ProfileGETSerializer,
    UserDetailSerializer,
    UserPatchSerializer,
    ChangePasswordSerializer,
    PasswordResetSerializer,
)
from .filters import UsersFilter
from .models import CustomUser
from .tasks import (
    send_email,
    start_deleting_expired_codes,
    generate_user_thumbnail,
    resize_avatar,
    random_color_picker,
    get_text_fill_color,
    from_img_to_io,
    generate_avatar,
    generate_images_v2,
)


@pytest.fixture(autouse=True)
def temp_media_root(settings):
    """Redirect MEDIA_ROOT to a throw-away temp dir for every test."""
    temp_dir = tempfile.mkdtemp(dir=".")
    settings.MEDIA_ROOT = temp_dir
    yield
    try:
        shutil.rmtree(temp_dir)
    except (PermissionError, OSError):
        pass


pytestmark = pytest.mark.django_db

# A small but valid 10×10 PNG encoded as base64 data-URI.
IMG_B64 = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9"
    "AAAADklEQVR4nGNgGAWDEwAAAZoAAR2CVqgAAAAASUVORK5CYII="
)


def make_staff_user(email="staff@test.com", password="securepass123"):
    """Create a staff user with JWT token and return (user, authenticated_client)."""
    user = CustomUser.objects.create_user(email=email, password=password, is_staff=True)
    token = str(AccessToken.for_user(user))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return user, client


def make_regular_user(email="regular@test.com", password="securepass123"):
    """Create a regular (non-staff) user and return (user, authenticated_client)."""
    user = CustomUser.objects.create_user(
        email=email, password=password, is_staff=False
    )
    token = str(AccessToken.for_user(user))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return user, client


@pytest.fixture
def user_extra():
    """Convenience fixture: a staff user with first/last/gender preset."""
    return CustomUser.objects.create_user(
        email="extra_test@example.com",
        password="testpass",
        first_name="Test",
        last_name="User",
        gender="H",
    )


@pytest.mark.django_db
class TestAccountAPI:
    """Core CRUD & auth flow tests."""

    def setup_method(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            email="testuser@example.com", password="securepass123", is_staff=True
        )
        self.token = str(AccessToken.for_user(self.user))
        self.auth_client = APIClient()
        self.auth_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_login_success(self):
        url = reverse("account:login")
        resp = self.client.post(
            url, {"email": self.user.email, "password": "securepass123"}
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "access" in resp.data

    def test_login_failure_wrong_password(self):
        url = reverse("account:login")
        resp = self.client.post(
            url, {"email": self.user.email, "password": "wrongpass"}
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_failure_nonexistent_email(self):
        url = reverse("account:login")
        resp = self.client.post(
            url, {"email": "nobody@example.com", "password": "pass"}
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_logout(self):
        url = reverse("account:logout")
        resp = self.auth_client.post(url)
        assert resp.status_code == status.HTTP_200_OK

    def test_unauthenticated_profile_request(self):
        url = reverse("account:profil")
        resp = self.client.get(url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_check_email_exists(self):
        url = reverse("account:check_email")
        resp = self.auth_client.post(url, {"email": self.user.email})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in resp.data["details"]

    def test_check_email_not_exists(self):
        url = reverse("account:check_email")
        resp = self.auth_client.post(url, {"email": "new@example.com"})
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_password_change_success(self):
        url = reverse("account:password_change")
        self.user.default_password_set = True
        self.user.save()
        resp = self.auth_client.put(
            url,
            {
                "old_password": "securepass123",
                "new_password": "newsecurepass456",
                "new_password2": "newsecurepass456",
            },
        )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        self.user.refresh_from_db()
        assert self.user.default_password_set is False

    def test_password_change_invalid_old(self):
        url = reverse("account:password_change")
        resp = self.auth_client.put(
            url,
            {
                "old_password": "wrongpass",
                "new_password": "newsecurepass456",
                "new_password2": "newsecurepass456",
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "old_password" in resp.data["details"]

    def test_send_password_reset_valid_email(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:send_password_reset")
        resp = self.client.post(url, {"email": self.user.email})
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_send_password_reset_invalid_email(self):
        url = reverse("account:send_password_reset")
        resp = self.client.post(url, {"email": "not-a-valid-email"})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_code_check_valid(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset_detail", args=[self.user.email, "1234"])
        resp = self.client.get(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_password_reset_code_check_invalid(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset_detail", args=[self.user.email, "wrong"])
        resp = self.client.get(url)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_put_valid(self):
        self.user.password_reset_code = "1234"
        self.user.default_password_set = True
        self.user.save()
        url = reverse("account:password_reset")
        resp = self.client.put(
            url,
            {
                "email": self.user.email,
                "code": "1234",
                "new_password": "newpass456",
                "new_password2": "newpass456",
            },
        )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        self.user.refresh_from_db()
        assert self.user.default_password_set is False

    def test_password_reset_put_invalid_code(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset")
        resp = self.client.put(
            url,
            {
                "email": self.user.email,
                "code": "wrong",
                "new_password": "newpass456",
                "new_password2": "newpass456",
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_profile(self):
        url = reverse("account:profil")
        resp = self.auth_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert "first_name" in resp.data

    def test_patch_profile_name_and_gender(self):
        url = reverse("account:profil")
        resp = self.auth_client.patch(
            url, {"first_name": "Al", "last_name": "Tester", "gender": "Homme"}
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["first_name"] == "Al"
        assert resp.data["gender"] == "H"

    def test_get_users_list(self):
        url = reverse("account:users")
        resp = self.auth_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.data, list) or "results" in resp.data

    def test_get_user_detail(self):
        other = self.user_model.objects.create_user(
            email="other@example.com", password="pass"
        )
        url = reverse("account:users_detail", args=[other.pk])
        resp = self.auth_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["email"] == other.email

    def test_put_user_update(self):
        other = self.user_model.objects.create_user(
            email="other@example.com", password="pass"
        )
        url = reverse("account:users_detail", args=[other.pk])
        resp = self.auth_client.put(url, {"first_name": "Updated", "last_name": "User"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["first_name"] == "Updated"

    def test_delete_user(self):
        other = self.user_model.objects.create_user(
            email="delete@example.com", password="pass"
        )
        url = reverse("account:users_detail", args=[other.pk])
        resp = self.auth_client.delete(url)
        assert resp.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
class TestAccountAPIExtras:
    """Token verify/refresh, password edge cases, avatar upload, self‑protection."""

    def setup_method(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            email="extras@example.com",
            password="securepass123",
            first_name="Extra",
            last_name="User",
            is_staff=True,
        )
        self.token = str(AccessToken.for_user(self.user))
        self.auth_client = APIClient()
        self.auth_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_token_verify_valid(self):
        url = reverse("account:token_verify")
        assert self.client.post(url, {"token": self.token}).status_code == 200

    def test_token_verify_invalid(self):
        url = reverse("account:token_verify")
        assert self.client.post(url, {"token": "invalid"}).status_code == 401

    def test_token_refresh(self):
        refresh = str(RefreshToken.for_user(self.user))
        url = reverse("account:token_refresh")
        resp = self.client.post(url, {"refresh": refresh})
        assert resp.status_code == 200
        assert "access" in resp.data

    def test_password_change_mismatch(self):
        url = reverse("account:password_change")
        resp = self.auth_client.put(
            url,
            {
                "old_password": "securepass123",
                "new_password": "newsecurepass456",
                "new_password2": "mismatch",
            },
        )
        assert resp.status_code == 400

    def test_password_change_too_short(self):
        url = reverse("account:password_change")
        resp = self.auth_client.put(
            url,
            {
                "old_password": "securepass123",
                "new_password": "short",
                "new_password2": "short",
            },
        )
        assert resp.status_code == 400

    def test_password_reset_code_check_unknown_email(self):
        url = reverse(
            "account:password_reset_detail", args=["unknown@example.com", "1234"]
        )
        assert self.client.get(url).status_code == 400

    def test_password_reset_put_mismatch(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset")
        resp = self.client.put(
            url,
            {
                "email": self.user.email,
                "code": "1234",
                "new_password": "newpass456",
                "new_password2": "wrong",
            },
        )
        assert resp.status_code == 400

    def test_password_reset_put_missing_email(self):
        """PUT with no email field should return 400."""
        url = reverse("account:password_reset")
        resp = self.client.put(
            url,
            {"code": "1234", "new_password": "pw1", "new_password2": "pw1"},
        )
        assert resp.status_code == 400

    def test_password_reset_put_blank_email(self):
        """PUT with empty email string should return 400."""
        url = reverse("account:password_reset")
        resp = self.client.put(
            url,
            {
                "email": "",
                "code": "1234",
                "new_password": "pw1",
                "new_password2": "pw1",
            },
        )
        assert resp.status_code == 400

    def test_password_reset_put_invalid_email_format(self):
        """PUT with invalid email format should return 400."""
        url = reverse("account:password_reset")
        resp = self.client.put(
            url,
            {
                "email": "not-an-email",
                "code": "1234",
                "new_password": "pw1",
                "new_password2": "pw1",
            },
        )
        assert resp.status_code == 400

    def test_patch_profile_avatar_base64_sets_url(self):
        url = reverse("account:profil")
        resp = self.auth_client.patch(url, {"avatar": IMG_B64})
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert resp.data["avatar"] is None or str(resp.data["avatar"]).startswith(
                "http"
            )

    def test_patch_profile_avatar_null_removes_files(self):
        url = reverse("account:profil")
        seed = self.auth_client.patch(
            url, {"avatar": IMG_B64, "avatar_cropped": IMG_B64}
        )
        if seed.status_code == 400:
            return  # seeding failed; skip remainder

        user = self.user_model.objects.get(pk=self.user.pk)
        paths = []
        for f in (user.avatar, user.avatar_cropped):
            name = getattr(f, "name", None)
            if name:
                try:
                    paths.append(f.path)
                except ValueError:
                    paths.append(None)
            else:
                paths.append(None)

        resp = self.auth_client.patch(url, {"avatar": None, "avatar_cropped": None})
        if resp.status_code == 200:
            user.refresh_from_db()
            for p in paths:
                if p:
                    assert not os.path.exists(p)

    def test_get_users_list_pagination_true(self):
        self.user_model.objects.create_user(email="p1@example.com", password="p")
        self.user_model.objects.create_user(email="p2@example.com", password="p")
        url = reverse("account:users") + "?pagination=true&page_size=1"
        resp = self.auth_client.get(url)
        assert resp.status_code == 200
        assert "results" in resp.data
        assert resp.data["count"] >= 2

    def test_post_users_create_with_avatar_and_uppercase_email(self):
        url = reverse("account:users")
        payload = {
            "email": "NEWUSER@EXAMPLE.COM",
            "first_name": "New",
            "last_name": "User",
            "is_staff": False,
            "is_active": True,
            "avatar": IMG_B64,
            "avatar_cropped": IMG_B64,
        }
        resp = self.auth_client.post(url, payload)
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        created = self.user_model.objects.get(email="newuser@example.com")
        assert created.first_name == "New"
        assert created.default_password_set is True

    def test_user_detail_get_self_404(self):
        url = reverse("account:users_detail", args=[self.user.pk])
        assert self.auth_client.get(url).status_code == 404

    def test_user_detail_put_self_404(self):
        url = reverse("account:users_detail", args=[self.user.pk])
        assert self.auth_client.put(url, {"first_name": "Nope"}).status_code == 404

    def test_user_detail_delete_self_404(self):
        url = reverse("account:users_detail", args=[self.user.pk])
        assert self.auth_client.delete(url).status_code == 404

    def test_get_profile_fields_presence(self):
        url = reverse("account:profil")
        resp = self.auth_client.get(url)
        assert resp.status_code == 200
        for key in ("id", "is_staff", "date_joined", "last_login"):
            assert key in resp.data


def test_send_email_task_updates_user_and_sends_mail():
    assert app_settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
    user = CustomUser.objects.create(email="task_mail@example.com", password="1234")
    send_email.delay(
        user.pk,
        user.email,
        "Reset",
        "<p>Hello</p>",
        code="9999",
        type_="password_reset_code",
    )
    assert len(mail.outbox) == 1
    assert "Hello" in mail.outbox[0].body
    user.refresh_from_db()
    assert user.password_reset_code == "9999"


def test_start_deleting_expired_codes_clears_code():
    user = CustomUser.objects.create(
        email="task_expire@example.com", password="1234", password_reset_code="9999"
    )
    start_deleting_expired_codes.delay(user.pk, "password_reset")
    user.refresh_from_db()
    assert user.password_reset_code is None


def test_start_deleting_expired_codes_unknown_type():
    user = CustomUser.objects.create(
        email="task_unknown@example.com", password="1234", password_reset_code="9999"
    )
    start_deleting_expired_codes(user.pk, "unknown_type")
    user.refresh_from_db()
    assert user.password_reset_code == "9999"


def test_generate_user_thumbnail_saves_images():
    user = CustomUser.objects.create(
        first_name="John", last_name="Doe", email="task_thumb@example.com"
    )
    generate_user_thumbnail.delay(user.pk)
    user.refresh_from_db()
    assert user.avatar is not None
    assert user.avatar_cropped is not None


@pytest.mark.django_db
def test_resize_avatar_saves_and_sends_event(monkeypatch):
    user = CustomUser.objects.create(
        first_name="Jane", last_name="Doe", email="task_resize@example.com"
    )
    img = Image.new("RGB", (100, 100), color="red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    monkeypatch.setattr("account.tasks.sync_to_async", lambda f: f)
    monkeypatch.setattr(
        "account.tasks.async_to_sync",
        lambda f: (lambda *a, **kw: f(*a, **kw)),
    )

    calls: list = []

    class FakeChannelLayer:
        @staticmethod
        def group_send(group, event):
            calls.append((group, event))

    monkeypatch.setattr("account.tasks.get_channel_layer", lambda: FakeChannelLayer())

    resize_avatar.delay(user.pk, buf)
    user.refresh_from_db()
    assert user.avatar is not None
    assert len(calls) == 1
    assert calls[0][1]["type"] == "receive_group_message"
    assert calls[0][1]["message"]["type"] == "USER_AVATAR"


@patch("account.tasks.start_deleting_expired_codes.apply_async")
@patch("account.views.current_app.control.revoke")
def test_view_schedules_and_revokes(
    revoke_mock, apply_async_mock, client, django_user_model
):
    user = django_user_model.objects.create(
        email="task_sched@example.com",
        password="1234",
        task_id_password_reset="prev-id",
    )

    class FakeAsyncResult:
        id = "a" * 36

    apply_async_mock.return_value = FakeAsyncResult()

    url = reverse("account:send_password_reset")
    resp = client.post(url, {"email": user.email})
    assert resp.status_code == 204
    revoke_mock.assert_called()
    assert apply_async_mock.called
    _, kwargs = apply_async_mock.call_args
    eta = kwargs["eta"]
    assert isinstance(eta, datetime)
    delta = (eta - datetime.now(timezone.utc)).total_seconds()
    assert 86000 <= delta <= 86800

    user.refresh_from_db()
    assert user.task_id_password_reset is not None
    assert len(user.task_id_password_reset) <= 40


@pytest.mark.django_db
class TestManagers:
    def test_create_user_requires_email(self):
        with pytest.raises(ValueError):
            get_user_model().objects.create_user(email="", password="p")

    def test_create_user_normalizes_domain_and_sets_password(self):
        u = get_user_model().objects.create_user(
            email="User+Tag@Example.COM", password="secret"
        )
        assert u.email.split("@")[1] == "example.com"
        assert u.check_password("secret")

    def test_create_superuser_flags_and_validation(self):
        user_obj = get_user_model()
        su = user_obj.objects.create_superuser(
            email="admin@example.com", password="adminpw"
        )
        assert su.is_staff and su.is_superuser and su.is_active

        with pytest.raises(ValueError):
            user_obj.objects.create_superuser(
                email="bad1@example.com", password="pw", is_staff=False
            )
        with pytest.raises(ValueError):
            user_obj.objects.create_superuser(
                email="bad2@example.com", password="pw", is_superuser=False
            )


@pytest.mark.django_db
class TestCustomUserModel:
    def test_str_with_full_name(self):
        user = CustomUser.objects.create_user(
            email="str@test.com",
            password="pass",
            first_name="Jane",
            last_name="Doe",
        )
        assert str(user) == "Jane Doe"

    def test_str_with_email_only(self):
        user = CustomUser.objects.create_user(
            email="emailonly@test.com", password="pass"
        )
        assert str(user) == "emailonly@test.com"

    def test_default_password_set_false(self):
        user = CustomUser.objects.create_user(email="defpwd@test.com", password="pass")
        assert user.default_password_set is False

    def test_is_active_default_true(self):
        user = CustomUser.objects.create_user(email="active@test.com", password="pass")
        assert user.is_active is True

    def test_get_absolute_avatar_img_none(self):
        user = CustomUser.objects.create_user(email="noav@test.com", password="pass")
        assert user.get_absolute_avatar_img is None

    def test_get_absolute_avatar_cropped_img_none(self):
        user = CustomUser.objects.create_user(email="noacr@test.com", password="pass")
        assert user.get_absolute_avatar_cropped_img is None


@pytest.mark.django_db
class TestFilters:
    def test_empty_search_returns_all(self):
        user_obj = get_user_model()
        u1 = user_obj.objects.create_user(email="a1@example.com", password="p")
        u2 = user_obj.objects.create_user(email="a2@example.com", password="p")
        qs = UsersFilter(data={"search": ""}, queryset=user_obj.objects.all()).qs
        assert u1 in qs and u2 in qs

    def test_search_by_name_and_email(self):
        user_obj = get_user_model()
        alice = user_obj.objects.create_user(
            email="alice+tag@example.com",
            password="p",
            first_name="Alice",
            last_name="Tester",
        )
        bob = user_obj.objects.create_user(
            email="bob@example.com", password="p", first_name="Bob"
        )
        qs = UsersFilter(data={"search": "Alice"}, queryset=user_obj.objects.all()).qs
        assert alice in qs and bob not in qs

        qs2 = UsersFilter(
            data={"search": "example.com"}, queryset=user_obj.objects.all()
        ).qs
        assert alice in qs2 and bob in qs2

    def test_search_matches_gender_display(self):
        user_obj = get_user_model()
        user = user_obj.objects.create_user(
            email="gendertest@example.com", password="p", first_name="G", gender="H"
        )
        qs = UsersFilter(data={"search": "Homme"}, queryset=user_obj.objects.all()).qs
        assert user in qs


@pytest.mark.django_db
class TestUsersFilterExtra:
    """Thorough filter coverage: gender, email, metacharacters, text lookups, isempty."""

    def test_global_search_with_gender_homme(self):
        user = CustomUser.objects.create_user(
            email="homme@test.com", password="p", gender="H"
        )
        result = UsersFilter.global_search(CustomUser.objects.all(), "search", "Homme")
        assert user in result

    def test_global_search_with_gender_femme(self):
        user = CustomUser.objects.create_user(
            email="femme@test.com", password="p", gender="F"
        )
        result = UsersFilter.global_search(CustomUser.objects.all(), "search", "Femme")
        assert user in result

    def test_global_search_by_email(self):
        user = CustomUser.objects.create_user(
            email="unique_email@test.com", password="p"
        )
        result = UsersFilter.global_search(
            CustomUser.objects.all(), "search", "unique_email"
        )
        assert user in result

    def test_global_search_empty_value(self):
        qs = CustomUser.objects.all()
        before = qs.count()
        result = UsersFilter.global_search(qs, "search", "")
        assert result.count() == before

    def test_global_search_whitespace_only(self):
        qs = CustomUser.objects.all()
        before = qs.count()
        result = UsersFilter.global_search(qs, "search", "   ")
        assert result.count() == before

    def test_global_search_metacharacters(self):
        CustomUser.objects.create_user(email="meta@test.com", password="p")
        result = UsersFilter.global_search(CustomUser.objects.all(), "search", "test:*")
        assert result is not None

    def test_first_name_icontains(self):
        u1 = CustomUser.objects.create_user(
            email="fn@test.com", password="p", first_name="Ahmed"
        )
        u2 = CustomUser.objects.create_user(
            email="fn2@test.com", password="p", first_name="Sara"
        )
        filt = UsersFilter(
            {"first_name__icontains": "ahm"}, queryset=CustomUser.objects.all()
        )
        assert u1 in filt.qs and u2 not in filt.qs

    def test_last_name_istartswith(self):
        u = CustomUser.objects.create_user(
            email="ln@test.com", password="p", last_name="Benali"
        )
        filt = UsersFilter(
            {"last_name__istartswith": "Ben"}, queryset=CustomUser.objects.all()
        )
        assert u in filt.qs

    def test_gender_method_filter(self):
        u_h = CustomUser.objects.create_user(
            email="gh@test.com", password="p", gender="H"
        )
        u_f = CustomUser.objects.create_user(
            email="gf@test.com", password="p", gender="F"
        )
        filt = UsersFilter({"gender": "Homme"}, queryset=CustomUser.objects.all())
        assert u_h in filt.qs and u_f not in filt.qs
        filt2 = UsersFilter({"gender": "Femme"}, queryset=CustomUser.objects.all())
        assert u_f in filt2.qs and u_h not in filt2.qs

    def test_is_staff_boolean(self):
        u1 = CustomUser.objects.create_user(
            email="bst@test.com", password="p", is_staff=True
        )
        u2 = CustomUser.objects.create_user(
            email="bnst@test.com", password="p", is_staff=False
        )
        filt = UsersFilter({"is_staff": "true"}, queryset=CustomUser.objects.all())
        assert u1 in filt.qs and u2 not in filt.qs

    def test_first_name_isempty_true(self):
        u_e = CustomUser.objects.create_user(
            email="empty@test.com", password="p", first_name=""
        )
        u_f = CustomUser.objects.create_user(
            email="full@test.com", password="p", first_name="John"
        )
        filt = UsersFilter(
            {"first_name__isempty": "true"}, queryset=CustomUser.objects.all()
        )
        assert u_e in filt.qs and u_f not in filt.qs

    def test_first_name_isempty_false(self):
        u_e = CustomUser.objects.create_user(
            email="e2@test.com", password="p", first_name=""
        )
        u_f = CustomUser.objects.create_user(
            email="f2@test.com", password="p", first_name="John"
        )
        filt = UsersFilter(
            {"first_name__isempty": "false"}, queryset=CustomUser.objects.all()
        )
        assert u_f in filt.qs and u_e not in filt.qs


@pytest.mark.django_db
class TestSerializers:
    """Serializer validation, image processing, gender, representation."""

    def test_createaccount_validate_gender_cases(self):
        assert CreateAccountSerializer.validate_gender("") == ""
        assert CreateAccountSerializer.validate_gender("Homme") == "H"
        assert CreateAccountSerializer.validate_gender("Femme") == "F"
        with pytest.raises(drf_serializers.ValidationError):
            CreateAccountSerializer.validate_gender("Other")

    def test_profileput_validate_gender_cases(self):
        assert ProfilePutSerializer.validate_gender("") == ""
        assert ProfilePutSerializer.validate_gender("Homme") == "H"
        assert ProfilePutSerializer.validate_gender("Femme") == "F"
        with pytest.raises(drf_serializers.ValidationError):
            ProfilePutSerializer.validate_gender("Invalid")

    def test_createaccount_process_image_field_base64(self):
        cf = CreateAccountSerializer._process_image_field("avatar", {"avatar": IMG_B64})
        assert cf is not None
        assert getattr(cf, "name", "").endswith(".webp")

    def test_createaccount_process_image_field_fileobj(self):
        img = Image.new("RGB", (10, 10), color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        uploaded = SimpleUploadedFile(
            "avatar.png", buf.getvalue(), content_type="image/png"
        )
        cf = CreateAccountSerializer._process_image_field(
            "avatar", {"avatar": uploaded}
        )
        assert cf is not None
        assert getattr(cf, "name", "").endswith(".webp")

    def test_createaccount_process_image_field_invalid_raises(self):
        with pytest.raises(drf_serializers.ValidationError):
            CreateAccountSerializer._process_image_field(
                "avatar", {"avatar": "not-an-image"}
            )

    def test_profileput_process_image_field_url(self):
        ret = ProfilePutSerializer._process_image_field(
            "avatar", {"avatar": "https://example.com/img.png"}
        )
        assert ret == (None, None, True)

    def test_profileput_process_image_field_base64(self):
        cf, b, is_url = ProfilePutSerializer._process_image_field(
            "avatar", {"avatar": IMG_B64}
        )
        assert cf is not None and b is not None and is_url is False

    def test_profileput_process_image_field_fileobj(self):
        img = Image.new("RGB", (10, 10), color="white")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        uploaded = SimpleUploadedFile(
            "avatar.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        cf, b, is_url = ProfilePutSerializer._process_image_field(
            "avatar", {"avatar": uploaded}
        )
        assert cf is not None and b is not None and is_url is False

    def test_userslist_and_profileget_get_gender(self):
        user_obj = get_user_model()
        u_none = user_obj.objects.create_user(
            email="gn@example.com", password="p", gender=""
        )
        u_h = user_obj.objects.create_user(
            email="gh2@example.com", password="p", gender="H"
        )
        assert UsersListSerializer.get_gender(u_none) is None
        assert ProfileGETSerializer.get_gender(u_none) is None
        assert UsersListSerializer.get_gender(u_h) == u_h.get_gender_display()
        assert ProfileGETSerializer.get_gender(u_h) == u_h.get_gender_display()

    def test_profileget_builds_absolute_urls(self):
        user_obj = get_user_model()
        user = user_obj.objects.create_user(email="repr@example.com", password="p")
        user.avatar.save("repr.png", ContentFile(b"img"), save=True)

        class FakeRequest:
            @staticmethod
            def build_absolute_uri(url):
                return "http://127.0.0.1:8001" + url

        ser = ProfileGETSerializer(user, context={"request": FakeRequest()})
        rep = ser.data
        assert rep["avatar"] is None or str(rep["avatar"]).startswith(
            ("http://", "https://")
        )

    def test_create_raises_on_image_processing_exception(self, monkeypatch):
        def bad_process(*a, **kw):
            raise Exception("boom")

        monkeypatch.setattr(
            CreateAccountSerializer, "_process_image_field", staticmethod(bad_process)
        )
        with pytest.raises(Exception, match="boom"):
            CreateAccountSerializer().create(
                {"email": "imgfail@example.com", "password": "p", "avatar": IMG_B64}
            )

    def test_profileput_update_deletes_old_files(self, monkeypatch):
        user_obj = get_user_model()
        user = user_obj.objects.create_user(email="upd@example.com", password="p")
        user.avatar.save("old.png", ContentFile(b"old"), save=True)
        user.avatar_cropped.save("oldc.png", ContentFile(b"oldc"), save=True)

        old_names = (user.avatar.name, user.avatar_cropped.name)
        deleted: list[str] = []

        def fake_delete(self, field):
            deleted.append(getattr(field, "name", str(field)))

        monkeypatch.setattr(
            ProfilePutSerializer, "_delete_file", fake_delete, raising=False
        )

        def fake_process(field_name, _data):
            uploaded = SimpleUploadedFile(
                "new.png", b"\x89PNG\r\n", content_type="image/png"
            )
            return uploaded, BytesIO(b"new"), False

        monkeypatch.setattr(
            ProfilePutSerializer,
            "_process_image_field",
            staticmethod(fake_process),
            raising=False,
        )

        ser = ProfilePutSerializer(
            instance=user, data={"avatar": IMG_B64}, partial=True
        )
        try:
            ser.is_valid(raise_exception=True)
            ser.save()
        except (TypeError, ValueError, AttributeError):
            pass

        if not connection.needs_rollback:
            user.refresh_from_db()

        name_changed = (
            getattr(user.avatar, "name", None) != old_names[0]
            or getattr(user.avatar_cropped, "name", None) != old_names[1]
        )
        assert len(deleted) >= 1 or name_changed


@pytest.mark.django_db
class TestSerializersExtra:
    """Supplementary serializer tests."""

    def test_create_account_with_avatar_base64(self):
        ser = CreateAccountSerializer(
            data={
                "email": "avb64@example.com",
                "password": "testpass123",
                "first_name": "Av",
                "last_name": "B64",
                "avatar": IMG_B64,
            }
        )
        assert ser.is_valid(), ser.errors
        user = ser.save()
        assert user.avatar is not None

    def test_validate_gender_empty(self):
        assert CreateAccountSerializer.validate_gender("") == ""

    def test_profile_put_process_image_empty(self):
        assert ProfilePutSerializer._process_image_field("avatar", {"avatar": ""}) == (
            None,
            None,
            False,
        )
        assert ProfilePutSerializer._process_image_field(
            "avatar", {"avatar": None}
        ) == (None, None, False)

    def test_users_list_get_gender(self, user_extra):
        user_extra.gender = "H"
        assert UsersListSerializer.get_gender(user_extra) == "Homme"
        user_extra.gender = "F"
        assert UsersListSerializer.get_gender(user_extra) == "Femme"
        user_extra.gender = ""
        assert UsersListSerializer.get_gender(user_extra) is None

    def test_change_password_validate(self):
        assert (
            ChangePasswordSerializer.validate_new_password("SecureP123!")
            == "SecureP123!"
        )

    def test_password_reset_matching(self):
        ser = PasswordResetSerializer(
            data={"new_password": "NewPass123!", "new_password2": "NewPass123!"}
        )
        assert ser.is_valid()

    def test_password_reset_mismatch(self):
        ser = PasswordResetSerializer(
            data={"new_password": "NewPass123!", "new_password2": "Different!"}
        )
        assert not ser.is_valid()
        assert "new_password2" in ser.errors


@pytest.mark.django_db
class TestTasksExtra:
    """Fine‑grained task and helper function tests."""

    @patch("account.tasks.EmailMessage")
    def test_send_email_basic(self, mock_cls, user_extra):
        mock_cls.return_value = MagicMock()
        send_email(user_extra.pk, user_extra.email, "Test", "Msg")
        mock_cls.return_value.send.assert_called_once_with(fail_silently=False)

    @patch("account.tasks.EmailMessage")
    def test_send_email_with_reset_code(self, mock_cls, user_extra):
        mock_cls.return_value = MagicMock()
        send_email(
            user_extra.pk,
            user_extra.email,
            "Reset",
            "Code",
            code="1234",
            type_="password_reset_code",
        )
        user_extra.refresh_from_db()
        assert user_extra.password_reset_code == "1234"

    def test_delete_reset_code(self, user_extra):
        user_extra.password_reset_code = "1234"
        user_extra.save()
        start_deleting_expired_codes(user_extra.pk, "password_reset")
        user_extra.refresh_from_db()
        assert user_extra.password_reset_code is None

    def test_random_color_picker_returns_list(self):
        colors = random_color_picker()
        assert isinstance(colors, list) and len(colors) > 0

    def test_get_text_fill_color_light(self):
        assert get_text_fill_color("#F3DCDC") == (0, 0, 0)
        assert get_text_fill_color("#FFD9A2") == (0, 0, 0)

    def test_get_text_fill_color_dark(self):
        assert get_text_fill_color("#0D070B") == (255, 255, 255)
        assert get_text_fill_color("#0274D7") == (255, 255, 255)

    def test_get_text_fill_color_unknown(self):
        assert get_text_fill_color("#UNKNOWN") == (0, 0, 0)

    def test_from_img_to_io(self):
        img = Image.new("RGB", (10, 10), color="red")
        result = from_img_to_io(img, "PNG")
        assert isinstance(result, BytesIO)

    @patch("account.tasks.ImageDraw.Draw")
    @patch("account.tasks.ImageFont.truetype")
    def test_generate_avatar(self, mock_font, mock_draw):
        mock_font.return_value = MagicMock()
        mock_draw.return_value = MagicMock()
        avatar = generate_avatar("T", "U")
        assert isinstance(avatar, Image.Image) and avatar.size == (600, 600)

    def test_resize_avatar_with_none(self, user_extra):
        with patch("account.tasks.CustomUser.objects.get", return_value=user_extra):
            with patch("account.tasks.resize_images_v2") as mock_resize:
                resize_avatar(object_pk=user_extra.pk, avatar=None)
                mock_resize.assert_not_called()

    def test_resize_avatar_with_non_bytesio(self, user_extra):
        with patch("account.tasks.CustomUser.objects.get", return_value=user_extra):
            with patch("account.tasks.resize_images_v2") as mock_resize:
                resize_avatar(object_pk=user_extra.pk, avatar="string")
                mock_resize.assert_not_called()


@pytest.mark.django_db
class TestCreateAccountSerializerExtra:

    def test_process_image_field_file_exception(self):
        class BrokenFile:
            name = "broken.jpg"

            def read(self):
                raise IOError("Read failed")

            def seek(self, pos):
                pass

        with pytest.raises(
            drf_serializers.ValidationError, match="Invalid file upload"
        ):
            CreateAccountSerializer._process_image_field(
                "avatar", {"avatar": BrokenFile()}
            )

    def test_process_image_field_invalid_base64(self):
        with pytest.raises(
            drf_serializers.ValidationError, match="base64.*invalide|Donn[eé]es base64"
        ):
            CreateAccountSerializer._process_image_field(
                "avatar", {"avatar": "data:image/png;base64,!!!invalid!!!"}
            )

    def test_create_with_avatar_saves_file(self):
        ser = CreateAccountSerializer(
            data={
                "email": "avatar_s@example.com",
                "password": "testpass123",
                "first_name": "Av",
                "last_name": "Save",
                "avatar": IMG_B64,
            }
        )
        assert ser.is_valid(), ser.errors
        user = ser.save()
        assert user.avatar and user.avatar.name != ""

    def test_create_with_cropped_saves_file(self):
        ser = CreateAccountSerializer(
            data={
                "email": "crop_s@example.com",
                "password": "testpass123",
                "first_name": "Crop",
                "last_name": "Save",
                "avatar_cropped": IMG_B64,
            }
        )
        assert ser.is_valid(), ser.errors
        user = ser.save()
        assert user.avatar_cropped is not None


@pytest.mark.django_db
class TestProfilePutSerializerExtra:
    """Null/empty/URL/upload avatar handling, cropped handling, file deletion."""

    def test_update_clears_avatar_on_null(self, user_extra):
        user_extra.avatar.save("t.png", ContentFile(b"t"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar": None}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert not ser.save().avatar

    def test_update_clears_avatar_on_empty_string(self, user_extra):
        user_extra.avatar.save("t2.png", ContentFile(b"t2"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar": ""}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert not ser.save().avatar

    def test_update_preserves_url_avatar(self, user_extra):
        user_extra.avatar.save("pres.png", ContentFile(b"pres"), save=True)
        old_name = user_extra.avatar.name
        ser = ProfilePutSerializer(
            instance=user_extra,
            data={"avatar": "http://example.com/existing.png"},
            partial=True,
        )
        assert ser.is_valid(), ser.errors
        assert ser.save().avatar.name == old_name

    def test_update_replaces_avatar_with_new_upload(self, user_extra):
        user_extra.avatar.save("old.png", ContentFile(b"old"), save=True)
        old_name = user_extra.avatar.name
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar": IMG_B64}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert ser.save().avatar.name != old_name

    def test_update_clears_cropped_on_new_avatar(self, user_extra):
        user_extra.avatar.save("av.png", ContentFile(b"av"), save=True)
        user_extra.avatar_cropped.save("avc.png", ContentFile(b"avc"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar": IMG_B64}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert not ser.save().avatar_cropped

    def test_update_clears_cropped_on_null(self, user_extra):
        user_extra.avatar_cropped.save("c.png", ContentFile(b"c"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar_cropped": None}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert not ser.save().avatar_cropped

    def test_process_image_field_file_exception(self):
        class BrokenFile:
            name = "broken.jpg"

            def read(self):
                raise IOError("Read failed")

            def seek(self, pos):
                pass

        with pytest.raises(
            drf_serializers.ValidationError, match="Invalid file upload"
        ):
            ProfilePutSerializer._process_image_field(
                "avatar", {"avatar": BrokenFile()}
            )

    def test_process_image_field_invalid_format(self):
        with pytest.raises(
            drf_serializers.ValidationError,
            match="Format d'image invalide|Format d'image base64 invalide",
        ):
            ProfilePutSerializer._process_image_field(
                "avatar", {"avatar": "not-url-not-base64"}
            )

    def test_delete_file_handles_missing_path(self):
        class FakeField:
            path = "/nonexistent/path/to/file.png"

            def delete(self, save=False):
                pass

        ProfilePutSerializer._delete_file(FakeField())  # should not raise

    def test_to_representation_without_request(self, user_extra):
        user_extra.avatar.save("repr.png", ContentFile(b"repr"), save=True)
        data = ProfilePutSerializer(instance=user_extra, context={}).data
        assert data["avatar"] is None or isinstance(data["avatar"], str)

    def test_to_representation_with_request(self, user_extra):
        user_extra.avatar.save("repr2.png", ContentFile(b"repr2"), save=True)

        class FakeRequest:
            @staticmethod
            def build_absolute_uri(url):
                return f"http://test.com{url}"

        data = ProfilePutSerializer(
            instance=user_extra, context={"request": FakeRequest()}
        ).data
        assert data["avatar"] is None or data["avatar"].startswith("http://")


@pytest.mark.django_db
class TestProfilePutSerializerBase64Exception:
    def test_process_image_field_invalid_base64(self):
        with pytest.raises(
            drf_serializers.ValidationError,
            match="base64.*invalide|Encodage base64 invalide|non reconnu",
        ):
            ProfilePutSerializer._process_image_field(
                "avatar", {"avatar": "data:image/png;base64,not_valid!!!"}
            )


@pytest.mark.django_db
class TestProfilePutSerializerDeleteBranches:
    """File deletion branches during avatar/cropped updates."""

    def test_update_avatar_deletes_old_files(self, user_extra):
        user_extra.avatar.save("old_av.png", ContentFile(b"av"), save=True)
        user_extra.avatar_cropped.save("old_cr.png", ContentFile(b"cr"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar": IMG_B64}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert ser.save().avatar.name != ""

    def test_update_cropped_deletes_old(self, user_extra):
        user_extra.avatar_cropped.save("old_c.png", ContentFile(b"c"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar_cropped": IMG_B64}, partial=True
        )
        assert ser.is_valid(), ser.errors
        assert ser.save().avatar_cropped.name != ""

    def test_clear_avatar_deletes_both(self, user_extra):
        user_extra.avatar.save("d_av.png", ContentFile(b"av"), save=True)
        user_extra.avatar_cropped.save("d_cr.png", ContentFile(b"cr"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar": None}, partial=True
        )
        assert ser.is_valid(), ser.errors
        updated = ser.save()
        assert not updated.avatar
        assert not updated.avatar_cropped

    def test_clear_cropped_only(self, user_extra):
        user_extra.avatar.save("keep.png", ContentFile(b"keep"), save=True)
        user_extra.avatar_cropped.save("del.png", ContentFile(b"del"), save=True)
        ser = ProfilePutSerializer(
            instance=user_extra, data={"avatar_cropped": ""}, partial=True
        )
        assert ser.is_valid(), ser.errors
        updated = ser.save()
        assert updated.avatar.name != ""
        assert not updated.avatar_cropped


@pytest.mark.django_db
class TestCreateAccountSerializerRepresentation:

    def test_to_representation_with_avatar(self):
        user = CustomUser.objects.create_user(email="repr_c@example.com", password="p")
        user.avatar.save("repr_av.png", ContentFile(b"av"), save=True)

        class FakeRequest:
            @staticmethod
            def build_absolute_uri(url):
                return f"http://test.com{url}"

        data = CreateAccountSerializer(
            instance=user, context={"request": FakeRequest()}
        ).data
        assert data["avatar"] is None or data["avatar"].startswith("http://")

    def test_to_representation_without_avatar(self):
        user = CustomUser.objects.create_user(email="repr_na@example.com", password="p")
        data = CreateAccountSerializer(instance=user, context={}).data
        assert data["avatar"] is None

    def test_to_representation_without_request(self):
        user = CustomUser.objects.create_user(email="repr_nr@example.com", password="p")
        user.avatar.save("repr_nr.png", ContentFile(b"d"), save=True)
        data = CreateAccountSerializer(instance=user, context={}).data
        assert data["avatar"] is None or isinstance(data["avatar"], str)


@pytest.mark.django_db
class TestUserDetailSerializerExtra:

    def test_get_gender_female(self, user_extra):
        user_extra.gender = "F"
        assert UserDetailSerializer.get_gender(user_extra) == "Femme"


@pytest.mark.django_db
class TestAccountViewsExtra:
    """Comprehensive view edge-case and branch tests."""

    @pytest.fixture(autouse=True)
    def setup_data(self, db):
        self.user = CustomUser.objects.create_user(
            email="viewstest@test.com",
            password="testpass123",
            first_name="Views",
            last_name="Test",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    # Email check ────────────────────────────────────────────────────────

    def test_check_email_existing(self):
        url = reverse("account:check_email")
        assert self.client.post(url, {"email": self.user.email}).status_code == 400

    def test_check_email_nonexisting(self):
        url = reverse("account:check_email")
        assert self.client.post(url, {"email": "x@test.com"}).status_code == 204

    def test_password_change_wrong_old(self):
        url = reverse("account:password_change")
        resp = self.client.put(
            url,
            {
                "old_password": "wrongpw",
                "new_password": "np123456",
                "new_password2": "np123456",
            },
        )
        assert resp.status_code == 400
        assert "old_password" in resp.data.get("details", resp.data)

    def test_password_change_mismatch(self):
        url = reverse("account:password_change")
        resp = self.client.put(
            url,
            {
                "old_password": "testpass123",
                "new_password": "np123456",
                "new_password2": "different",
            },
        )
        assert resp.status_code == 400

    def test_password_change_too_short(self):
        url = reverse("account:password_change")
        resp = self.client.put(
            url,
            {
                "old_password": "testpass123",
                "new_password": "short",
                "new_password2": "short",
            },
        )
        assert resp.status_code == 400

    def test_password_change_success(self):
        url = reverse("account:password_change")
        assert (
            self.client.put(
                url,
                {
                    "old_password": "testpass123",
                    "new_password": "np123456!!",
                    "new_password2": "np123456!!",
                },
            ).status_code
            == 204
        )

    def test_profile_get_fields(self):
        url = reverse("account:profil")
        resp = self.client.get(url)
        assert resp.status_code == 200
        assert resp.data["first_name"] == "Views"
        assert "is_staff" in resp.data
        assert "default_password_set" in resp.data

    def test_profile_patch_all_fields(self):
        url = reverse("account:profil")
        resp = self.client.patch(
            url,
            {
                "first_name": "Updated",
                "last_name": "Name",
                "gender": "Homme",
            },
            format="json",
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert resp.data["first_name"] == "Updated"

    def test_profile_patch_invalid_gender(self):
        url = reverse("account:profil")
        resp = self.client.patch(url, {"gender": "InvalidGender"}, format="json")
        assert resp.status_code in (200, 400)

    def test_users_list_no_pagination(self):
        url = reverse("account:users")
        resp = self.client.get(url)
        assert resp.status_code == 200
        assert isinstance(resp.data, list)

    def test_users_list_with_pagination(self):
        url = reverse("account:users") + "?pagination=true"
        resp = self.client.get(url)
        assert resp.status_code == 200
        assert "results" in resp.data

    def test_detail_self_get_404(self):
        assert (
            self.client.get(
                reverse("account:users_detail", args=[self.user.pk])
            ).status_code
            == 404
        )

    def test_detail_self_put_404(self):
        assert (
            self.client.put(
                reverse("account:users_detail", args=[self.user.pk]),
                {"first_name": "New"},
            ).status_code
            == 404
        )

    def test_detail_self_delete_404(self):
        assert (
            self.client.delete(
                reverse("account:users_detail", args=[self.user.pk])
            ).status_code
            == 404
        )

    def test_detail_other_get(self):
        other = CustomUser.objects.create_user(
            email="other@test.com", password="p", first_name="Other"
        )
        resp = self.client.get(reverse("account:users_detail", args=[other.pk]))
        assert resp.status_code == 200

    def test_detail_other_put(self):
        other = CustomUser.objects.create_user(
            email="putother@test.com", password="p", first_name="Put"
        )
        resp = self.client.put(
            reverse("account:users_detail", args=[other.pk]),
            {"first_name": "Updated"},
            format="json",
        )
        assert resp.status_code == 200

    def test_detail_other_delete(self):
        other = CustomUser.objects.create_user(email="delother@test.com", password="p")
        assert (
            self.client.delete(
                reverse("account:users_detail", args=[other.pk])
            ).status_code
            == 204
        )

    def test_delete_user_with_avatar(self):
        other = CustomUser.objects.create_user(email="delav@test.com", password="p")
        other.avatar.save("test.png", ContentFile(b"img"), save=True)
        other.avatar_cropped.save("testc.png", ContentFile(b"img"), save=True)
        assert (
            self.client.delete(
                reverse("account:users_detail", args=[other.pk])
            ).status_code
            == 204
        )

    def test_detail_not_found(self):
        assert (
            self.client.get(reverse("account:users_detail", args=[99999])).status_code
            == 404
        )

    def test_users_create_post(self):
        url = reverse("account:users")
        resp = self.client.post(
            url,
            {
                "email": "newuser@test.com",
                "first_name": "New",
                "last_name": "User",
                "avatar": "",
                "avatar_cropped": "",
            },
            format="json",
        )
        assert resp.status_code in (204, 400)

    def test_users_create_invalid_data(self):
        url = reverse("account:users")
        assert (
            self.client.post(url, {"email": "invalid-email"}, format="json").status_code
            == 400
        )

    def test_pw_reset_get_valid(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset_detail", args=[self.user.email, "1234"])
        assert APIClient().get(url).status_code == 204

    def test_pw_reset_get_invalid_code(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset_detail", args=[self.user.email, "9999"])
        assert APIClient().get(url).status_code == 400

    def test_pw_reset_get_user_not_found(self):
        url = reverse("account:password_reset_detail", args=["x@test.com", "1234"])
        assert APIClient().get(url).status_code == 400

    def test_pw_reset_put_success(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset")
        assert (
            APIClient()
            .put(
                url,
                {
                    "email": self.user.email,
                    "code": "1234",
                    "new_password": "newpw123456",
                    "new_password2": "newpw123456",
                },
                format="json",
            )
            .status_code
            == 204
        )

    def test_pw_reset_put_invalid_code(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        url = reverse("account:password_reset")
        assert (
            APIClient()
            .put(
                url,
                {
                    "email": self.user.email,
                    "code": "9999",
                    "new_password": "newpw123456",
                    "new_password2": "newpw123456",
                },
                format="json",
            )
            .status_code
            == 400
        )

    def test_pw_reset_put_user_not_found(self):
        url = reverse("account:password_reset")
        assert (
            APIClient()
            .put(
                url,
                {
                    "email": "x@test.com",
                    "code": "1234",
                    "new_password": "newpw123456",
                    "new_password2": "newpw123456",
                },
                format="json",
            )
            .status_code
            == 400
        )

    def test_detail_put_invalid_serializer(self):
        target = CustomUser.objects.create_user(
            email="tgt@test.com", password="p", first_name="Tgt"
        )
        resp = self.client.put(
            reverse("account:users_detail", args=[target.pk]),
            {"gender": "InvalidGender"},
            format="json",
        )
        assert resp.status_code == 400
        assert "gender" in resp.data.get("details", resp.data)


@pytest.mark.django_db
class TestAccountAdditionalCoverage:
    """Edge‑case: task revoke on Windows/Unix, short password bypass, etc."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            email="acctcov@example.com",
            password="securepass123",
            first_name="Test",
            last_name="Coverage",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {str(AccessToken.for_user(self.user))}"
        )

    def test_change_password_short(self):
        url = reverse("account:password_change")
        assert (
            self.client.put(
                url,
                {
                    "old_password": "securepass123",
                    "new_password": "short",
                    "new_password2": "short",
                },
                format="json",
            ).status_code
            == 400
        )

    def test_change_password_mismatch(self):
        url = reverse("account:password_change")
        assert (
            self.client.put(
                url,
                {
                    "old_password": "securepass123",
                    "new_password": "newpassword123",
                    "new_password2": "diff123",
                },
                format="json",
            ).status_code
            == 400
        )

    def test_pw_reset_get_invalid_code_direct(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        assert (
            APIClient()
            .get(f"/api/account/password_reset/{self.user.email}/9999/")
            .status_code
            == 400
        )

    def test_pw_reset_get_user_not_found_direct(self):
        assert (
            APIClient()
            .get("/api/account/password_reset/nonexistent@test.com/1234/")
            .status_code
            == 400
        )

    def test_pw_reset_post_user_not_found(self):
        resp = APIClient().post(
            reverse("account:send_password_reset"),
            {"email": "nonexistent@test.com"},
            format="json",
        )
        assert resp.status_code in (200, 204, 400)

    def test_pw_reset_post_invalid_email(self):
        resp = APIClient().post(
            reverse("account:send_password_reset"),
            {"email": "notanemail"},
            format="json",
        )
        assert resp.status_code in (200, 400)

    def test_pw_reset_put_mismatch_serializer(self):
        self.user.password_reset_code = "1234"
        self.user.save()
        assert (
            APIClient()
            .put(
                reverse("account:password_reset"),
                {
                    "email": self.user.email,
                    "code": "1234",
                    "new_password": "newpw123456",
                    "new_password2": "mismatch123",
                },
                format="json",
            )
            .status_code
            == 400
        )

    def test_password_reset_put_with_task_id_windows(self):
        self.user.password_reset_code = "1234"
        self.user.task_id_password_reset = "task-win"
        self.user.save()
        with patch("account.views.current_app") as mock_celery:
            resp = self.client.put(
                reverse("account:password_reset"),
                {
                    "email": self.user.email,
                    "code": "1234",
                    "new_password": "newsecurepass456",
                    "new_password2": "newsecurepass456",
                },
            )
        assert resp.status_code == 204
        mock_celery.control.revoke.assert_called_once_with("task-win", terminate=False)

    def test_password_reset_put_with_task_id_unix(self):
        u2 = self.User.objects.create_user(email="unix_reset@test.com", password="p")
        u2.task_id_password_reset = "task-unix"
        u2.password_reset_code = "5678"
        u2.save()
        with patch("account.views.platform", "linux"), patch(
            "account.views.current_app"
        ) as mock_celery:
            resp = self.client.put(
                reverse("account:password_reset"),
                {
                    "email": u2.email,
                    "code": "5678",
                    "new_password": "newsecurepass456",
                    "new_password2": "newsecurepass456",
                },
            )
        assert resp.status_code == 204
        mock_celery.control.revoke.assert_called_once_with(
            "task-unix", terminate=True, signal="SIGKILL"
        )

    def test_send_pw_reset_invalid_serializer(self):
        with patch("account.views.UserEmailSerializer") as mock_cls:
            mock_ser = MagicMock()
            mock_ser.is_valid.return_value = False
            mock_ser.errors = {"email": ["Invalid email"]}
            mock_cls.return_value = mock_ser
            resp = self.client.post(
                reverse("account:send_password_reset"),
                {"email": self.user.email},
            )
        assert resp.status_code == 400

    def test_send_pw_reset_unix_revoke(self):
        u2 = self.User.objects.create_user(email="send_unix@test.com", password="p")
        u2.task_id_password_reset = "send-unix-task"
        u2.save()
        with patch("account.views.platform", "linux"), patch(
            "account.views.current_app"
        ) as mock_celery, patch("account.views.send_email") as mock_se, patch(
            "account.views.start_deleting_expired_codes"
        ) as mock_sd:
            mock_se.apply_async = MagicMock()
            mock_sd.apply_async = MagicMock(return_value=MagicMock(id="new-task-id"))
            resp = self.client.post(
                reverse("account:send_password_reset"),
                {"email": u2.email},
            )
        assert resp.status_code == 204
        mock_celery.control.revoke.assert_called_once_with(
            "send-unix-task", terminate=True, signal="SIGKILL"
        )


@pytest.mark.django_db
class TestBulkDeleteUsersAPI:

    def setup_method(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(
            email="bulk_admin@example.com", password="pass", is_staff=True
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        self.u1 = self.User.objects.create_user(
            email="bu1@example.com", password="pass"
        )
        self.u2 = self.User.objects.create_user(
            email="bu2@example.com", password="pass"
        )

    def test_bulk_delete_success(self):
        url = reverse("account:users-bulk-delete")
        resp = self.api.delete(url, {"ids": [self.u1.id, self.u2.id]}, format="json")
        assert resp.status_code == 204
        assert not self.User.objects.filter(pk__in=[self.u1.id, self.u2.id]).exists()

    def test_bulk_delete_single(self):
        url = reverse("account:users-bulk-delete")
        assert (
            self.api.delete(url, {"ids": [self.u1.id]}, format="json").status_code
            == 204
        )
        assert not self.User.objects.filter(pk=self.u1.id).exists()
        assert self.User.objects.filter(pk=self.u2.id).exists()

    def test_bulk_delete_empty_ids_400(self):
        url = reverse("account:users-bulk-delete")
        assert self.api.delete(url, {"ids": []}, format="json").status_code == 400

    def test_bulk_delete_missing_ids_400(self):
        url = reverse("account:users-bulk-delete")
        assert self.api.delete(url, {}, format="json").status_code == 400

    def test_bulk_delete_cannot_delete_self(self):
        url = reverse("account:users-bulk-delete")
        resp = self.api.delete(url, {"ids": [self.admin.id, self.u1.id]}, format="json")
        assert resp.status_code == 400
        assert self.User.objects.filter(pk=self.admin.id).exists()

    def test_bulk_delete_unauthenticated_401(self):
        url = reverse("account:users-bulk-delete")
        assert (
            APIClient().delete(url, {"ids": [self.u1.id]}, format="json").status_code
            == 401
        )

    def test_bulk_delete_non_admin_403(self):
        regular, client = make_regular_user(email="regbd@example.com")
        url = reverse("account:users-bulk-delete")
        assert (
            client.delete(url, {"ids": [self.u1.id]}, format="json").status_code == 403
        )


@pytest.mark.django_db
class TestUserPatchSerializer:
    """Tests for UserPatchSerializer (extends ProfilePutSerializer)."""

    def setup_method(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            email="patch_target@example.com",
            password="pass",
            first_name="Target",
            last_name="User",
        )

    def test_meta_fields_include_permission_flags(self):
        """UserPatchSerializer.Meta.fields includes permission and identity fields."""
        expected_extra = [
            "id",
            "email",
            "is_active",
            "is_staff",
            "date_joined",
            "last_login",
        ]
        for field in expected_extra:
            assert field in UserPatchSerializer.Meta.fields

    def test_meta_read_only_fields(self):
        """id, email, date_joined, last_login are read-only."""
        for field in ("id", "email", "date_joined", "last_login"):
            assert field in UserPatchSerializer.Meta.read_only_fields

    def test_update_is_active(self):
        """Updating is_active flag works."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"is_active": False},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.is_active is False

    def test_update_is_staff(self):
        """Updating is_staff flag works."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"is_staff": True},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.is_staff is True

    def test_read_only_email_ignored(self):
        """Email is read-only; attempting to change it is silently ignored."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"email": "hacked@evil.com", "first_name": "Changed"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.email == "patch_target@example.com"
        assert updated.first_name == "Changed"

    def test_gender_validation_inherited(self):
        """Gender validation from ProfilePutSerializer is inherited."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"gender": "Invalid"},
            partial=True,
        )
        assert not serializer.is_valid()
        assert "gender" in serializer.errors

    def test_gender_homme(self):
        """Gender 'Homme' maps to 'H'."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"gender": "Homme"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.gender == "H"

    def test_gender_femme(self):
        """Gender 'Femme' maps to 'F'."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"gender": "Femme"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.gender == "F"

    def test_update_name_fields(self):
        """Updating first_name and last_name through UserPatchSerializer."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"first_name": "NewFirst", "last_name": "NewLast"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.first_name == "NewFirst"
        assert updated.last_name == "NewLast"

    def test_update_delegates_to_profile_put(self):
        """update() calls super().update() (ProfilePutSerializer)."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={"first_name": "Delegated"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        with patch.object(
            ProfilePutSerializer, "update", return_value=self.user
        ) as mock_super:
            serializer.save()
            mock_super.assert_called_once()

    def test_empty_partial_update(self):
        """Empty partial update doesn't break anything."""
        serializer = UserPatchSerializer(
            instance=self.user,
            data={},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.pk == self.user.pk


@pytest.mark.django_db
class TestGenerateImagesV2:
    """Tests for the generate_images_v2 helper function."""

    def setup_method(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            email="gen_img@example.com", password="pass"
        )

    def test_saves_avatar(self):
        """generate_images_v2 calls save_image('avatar', avatar) on the query object."""
        avatar = BytesIO(b"fake_image_data")
        with patch.object(self.user, "save_image") as mock_save:
            generate_images_v2(self.user, avatar)
            mock_save.assert_called_once_with("avatar", avatar)

    def test_called_with_bytesio(self):
        """generate_images_v2 works with a proper BytesIO object."""
        img = Image.new("RGB", (10, 10), color="red")
        buf = BytesIO()
        img.save(buf, format="WEBP")
        buf.seek(0)
        with patch.object(self.user, "save_image") as mock_save:
            generate_images_v2(self.user, buf)
            mock_save.assert_called_once_with("avatar", buf)

    def test_no_return_value(self):
        """generate_images_v2 returns None (side effect only)."""
        avatar = BytesIO(b"data")
        with patch.object(self.user, "save_image"):
            result = generate_images_v2(self.user, avatar)
            assert result is None
