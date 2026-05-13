from io import BytesIO
from os import path
from uuid import uuid4

from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from design_workflow_backend.settings import API_URL
from .managers import CustomUserManager


def get_avatar_path(_, filename):
    _, ext = path.splitext(filename)
    return path.join("user_avatars/", str(uuid4()) + ext)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    class UserRole(models.TextChoices):
        MANAGER = "manager", _("Manager")
        DESIGNER = "designer", _("Designer")

    email = models.EmailField(
        _("Adresse e‑mail"), unique=True, help_text=_("Adresse e‑mail de l'utilisateur")
    )
    sso_subject = models.CharField(
        _("Identifiant SSO"),
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Identifiant stable du compte central E.B.H."),
    )
    first_name = models.CharField(
        _("Prénom"), max_length=30, blank=True, help_text=_("Prénom de l'utilisateur")
    )
    last_name = models.CharField(
        _("Nom"),
        max_length=30,
        blank=True,
        help_text=_("Nom de famille de l'utilisateur"),
    )
    GENDER_CHOICES = (("", _("Unset")), ("H", _("Homme")), ("F", _("Femme")))
    gender = models.CharField(
        verbose_name=_("Sexe"),
        max_length=1,
        choices=GENDER_CHOICES,
        default="",
        help_text=_("Sexe de l'utilisateur"),
    )
    avatar = models.ImageField(
        verbose_name=_("Photo de profil"),
        upload_to=get_avatar_path,
        blank=True,
        null=True,
        default=None,
        help_text=_("Image de profil de l'utilisateur"),
    )
    avatar_cropped = models.ImageField(
        upload_to=get_avatar_path,
        blank=True,
        null=True,
        default=None,
        verbose_name=_("Photo de profil recadrée"),
        max_length=1000,
        help_text=_("Version recadrée de la photo de profil"),
    )
    is_staff = models.BooleanField(
        _("Statut personnel"),
        default=False,
        db_index=True,
        help_text=_(
            "Indique si l'utilisateur peut se connecter au panneau d'administration."
        ),
    )
    is_active = models.BooleanField(
        _("Actif"),
        default=True,
        db_index=True,
        help_text=_("Indique si ce compte doit être considéré comme actif."),
    )
    role = models.CharField(
        _("Rôle"),
        max_length=16,
        choices=UserRole.choices,
        default=UserRole.MANAGER,
        db_index=True,
        help_text=_("Rôle métier utilisé dans Design Workflow."),
    )
    date_joined = models.DateTimeField(
        _("Date d'inscription"),
        default=timezone.now,
        db_index=True,
        help_text=_("Horodatage de l'inscription de l'utilisateur."),
    )
    date_updated = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Date de modification"),
        db_index=True,
        help_text=_("Horodatage de la dernière modification du profil."),
    )
    password_reset_code = models.CharField(
        verbose_name=_("Mot de passe - Code de réinitialisation"),
        blank=True,
        null=True,
        db_index=True,
        help_text=_(
            "Code de réinitialisation du mot de passe (généré automatiquement)."
        ),
    )
    password_reset_code_created_at = models.DateTimeField(
        verbose_name=_("Mot de passe - Date de création du code"),
        blank=True,
        null=True,
        db_index=True,
        help_text=_("Date à laquelle le code de réinitialisation a été créé."),
    )
    task_id_password_reset = models.CharField(
        verbose_name=_("Mot de passe - Task ID de réinitialisation"),
        max_length=40,
        default=None,
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "Identifiant de la tâche Celery de réinitialisation du mot de passe."
        ),
    )
    default_password_set = models.BooleanField(
        verbose_name=_("Mot de passe par défaut défini"),
        default=False,
        db_index=True,
        help_text=_("Indique si le mot de passe par défaut a été défini."),
    )
    # Per-user permission flags (staff users bypass these checks)
    can_view = models.BooleanField(_("Peut consulter"), default=True)
    can_print = models.BooleanField(_("Peut imprimer"), default=True)
    can_create = models.BooleanField(_("Peut créer"), default=False)
    can_edit = models.BooleanField(_("Peut modifier"), default=False)
    can_delete = models.BooleanField(_("Peut supprimer"), default=False)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()
    history = HistoricalRecords(
        verbose_name=_("Historique Utilisateur"),
        verbose_name_plural=_("Historiques Utilisateurs"),
    )

    def __str__(self):
        full_name = "{} {}".format(self.first_name, self.last_name).strip()
        return full_name if full_name else self.email

    @property
    def get_absolute_avatar_img(self):
        if self.avatar:
            return f"{API_URL}{self.avatar.url}"
        return None

    @property
    def get_absolute_avatar_cropped_img(self):
        if self.avatar_cropped:
            return f"{API_URL}{self.avatar_cropped.url}"
        return None

    class Meta:
        verbose_name = _("Utilisateur")
        verbose_name_plural = _("Utilisateurs")
        ordering = ("-date_joined",)

    def save(self, *args, **kwargs):
        if not self.role:
            self.role = self.UserRole.MANAGER if self.is_staff else self.UserRole.DESIGNER
        elif self.is_staff and self.role == self.UserRole.DESIGNER:
            self.role = self.UserRole.MANAGER
        super().save(*args, **kwargs)

    def save_image(self, file_name, image):
        if not isinstance(image, BytesIO):
            return
        getattr(self, file_name).save(
            f"{str(uuid4())}.webp", ContentFile(image.getvalue()), save=True
        )
