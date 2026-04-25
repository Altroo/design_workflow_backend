from base64 import b64decode
from io import BytesIO
from os import remove
from pathlib import Path

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext as _
from rest_framework import serializers

from design_workflow_backend.utils import ImageProcessor
from .models import CustomUser


def normalize_gender_value(value):
    if value in (None, ''):
        return ''
    normalized = str(value).strip().lower()
    if normalized in {'h', 'homme'}:
        return 'H'
    if normalized in {'f', 'femme'}:
        return 'F'
    raise serializers.ValidationError(
        _("Valeur du sexe invalide : %(value)s. Doit etre 'Homme' ou 'Femme'.")
        % {'value': value}
    )


class CreateAccountSerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    avatar_cropped = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    gender = serializers.CharField(required=False, allow_blank=True)

    @staticmethod
    def validate_gender(value):
        return normalize_gender_value(value)

    @staticmethod
    def _process_image_field(field_name, validated_data):
        field_value = validated_data.get(field_name)
        if not field_value:
            return None
        if hasattr(field_value, "read"):
            try:
                field_value.seek(0)
                data = field_value.read()
                result = ImageProcessor.convert_to_webp(data)
                if result is None:
                    raise serializers.ValidationError(
                        _("Format d'image non reconnu pour %(field)s")
                        % {"field": field_name}
                    )
                return result
            except Exception as e:
                raise serializers.ValidationError(
                    f"Invalid file upload for {field_name}: {str(e)}"
                )
        if isinstance(field_value, str) and field_value.startswith("data:image"):
            try:
                if ";base64," not in field_value:
                    raise serializers.ValidationError(
                        f"Format d'image base64 invalide pour {field_name}"
                    )
                parts = field_value.split(";base64,", 1)
                if len(parts) != 2:
                    raise serializers.ValidationError(
                        f"Données base64 mal formées pour {field_name}"
                    )
                format_, imgstr = parts
                if not format_.startswith("data:image/"):
                    raise serializers.ValidationError(
                        f"Type MIME d'image invalide pour {field_name}"
                    )
                max_base64_length = getattr(
                    settings, "MAX_BASE64_IMAGE_SIZE", 15 * 1024 * 1024
                )
                if len(imgstr) > max_base64_length:
                    raise serializers.ValidationError(
                        f"Image trop grande pour {field_name}: {len(imgstr)} octets "
                        f"(max {max_base64_length}). "
                        f"Veuillez télécharger une image plus petite."
                    )
                try:
                    data = b64decode(imgstr)
                except Exception as decode_error:
                    raise serializers.ValidationError(
                        f"Encodage base64 invalide pour {field_name}: {str(decode_error)}"
                    )
                result = ImageProcessor.convert_to_webp(data)
                if result is None:
                    raise serializers.ValidationError(
                        f"Format d'image non reconnu pour {field_name}"
                    )
                return result
            except serializers.ValidationError:
                raise
            except Exception as e:
                raise serializers.ValidationError(
                    f"Données d'image base64 invalides pour {field_name}: {str(e)}"
                )
        raise serializers.ValidationError(f"Format d'image invalide pour {field_name}")

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        avatar = self._process_image_field("avatar", validated_data)
        avatar_cropped = self._process_image_field("avatar_cropped", validated_data)
        validated_data.pop("avatar", None)
        validated_data.pop("avatar_cropped", None)
        if "role" not in validated_data:
            validated_data["role"] = (
                CustomUser.UserRole.MANAGER
                if validated_data.get("is_staff")
                else CustomUser.UserRole.DESIGNER
            )

        instance = CustomUser(**validated_data)
        if password:
            instance.set_password(password)
        if avatar:
            instance.avatar.save(avatar.name, avatar, save=False)
        if avatar_cropped:
            instance.avatar_cropped.save(
                avatar_cropped.name, avatar_cropped, save=False
            )
        instance.save()
        return instance

    class Meta:
        model = CustomUser
        fields = [
            "email",
            "password",
            "first_name",
            "last_name",
            "gender",
            "avatar",
            "avatar_cropped",
            "is_staff",
            "is_active",
            "default_password_set",
            "role",
            "can_view",
            "can_print",
            "can_create",
            "can_edit",
            "can_delete",
        ]
        extra_kwargs = {
            "password": {"write_only": True},
            "default_password_set": {"default": False},
        }

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        request = self.context.get("request")
        for field in ["avatar", "avatar_cropped"]:
            if getattr(instance, field):
                if request:
                    representation[field] = request.build_absolute_uri(
                        getattr(instance, field).url
                    )
                else:
                    representation[field] = getattr(instance, field).url
            else:
                representation[field] = None
        return representation


class ChangePasswordSerializer(serializers.Serializer):
    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass

    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)
    new_password2 = serializers.CharField(required=True)

    @staticmethod
    def validate_new_password(value):
        validate_password(value)
        return value


class PasswordResetSerializer(serializers.Serializer):
    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass

    new_password = serializers.CharField(required=True)
    new_password2 = serializers.CharField(required=True)

    def validate(self, attrs):
        if attrs.get("new_password") != attrs.get("new_password2"):
            raise serializers.ValidationError(
                {"new_password2": "Les mots de passe ne correspondent pas."}
            )
        return attrs


class UserEmailSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(max_length=255)

    class Meta:
        model = CustomUser
        fields = ["email"]
        extra_kwargs = {"email": {"write_only": True}}


class ProfileGETSerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(source="get_absolute_avatar_img")
    avatar_cropped = serializers.CharField(source="get_absolute_avatar_cropped_img")
    gender = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(format="%d/%m/%Y")

    @staticmethod
    def get_gender(instance):
        if instance.gender != "":
            return instance.get_gender_display()
        return None

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "gender",
            "avatar",
            "avatar_cropped",
            "is_staff",
            "default_password_set",
            "role",
            "date_joined",
            "last_login",
            "can_view",
            "can_print",
            "can_create",
            "can_edit",
            "can_delete",
        ]


class ProfilePutSerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    avatar_cropped = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    gender = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = CustomUser
        fields = ["first_name", "last_name", "gender", "avatar", "avatar_cropped"]

    @staticmethod
    def validate_gender(value):
        return normalize_gender_value(value)

    @staticmethod
    def _process_image_field(field_name, validated_data):
        field_value = validated_data.get(field_name)
        if not field_value:
            return None, None, False
        if isinstance(field_value, str) and field_value.startswith("http"):
            return None, None, True
        if hasattr(field_value, "read"):
            try:
                field_value.seek(0)
                data = field_value.read()
                field_value.seek(0)
                webp_file = ImageProcessor.convert_to_webp(data)
                if webp_file is None:
                    raise serializers.ValidationError(
                        f"Format d'image non reconnu pour {field_name}"
                    )
                return webp_file, BytesIO(data), False
            except Exception as e:
                raise serializers.ValidationError(
                    f"Invalid file upload for {field_name}: {str(e)}"
                )
        if isinstance(field_value, str) and field_value.startswith("data:image"):
            try:
                if ";base64," not in field_value:
                    raise serializers.ValidationError(
                        f"Format d'image base64 invalide pour {field_name}"
                    )
                parts = field_value.split(";base64,", 1)
                if len(parts) != 2:
                    raise serializers.ValidationError(
                        f"Données base64 mal formées pour {field_name}"
                    )
                format_, imgstr = parts
                if not format_.startswith("data:image/"):
                    raise serializers.ValidationError(
                        f"Type MIME d'image invalide pour {field_name}"
                    )
                max_base64_length = getattr(
                    settings, "MAX_BASE64_IMAGE_SIZE", 15 * 1024 * 1024
                )
                if len(imgstr) > max_base64_length:
                    raise serializers.ValidationError(
                        f"Image trop grande pour {field_name}: {len(imgstr)} octets "
                        f"(max {max_base64_length}). "
                        f"Veuillez télécharger une image plus petite."
                    )
                try:
                    data = b64decode(imgstr)
                except Exception as decode_error:
                    raise serializers.ValidationError(
                        f"Encodage base64 invalide pour {field_name}: {str(decode_error)}"
                    )
                webp_file = ImageProcessor.convert_to_webp(data)
                if webp_file is None:
                    raise serializers.ValidationError(
                        f"Format d'image non reconnu pour {field_name}"
                    )
                return webp_file, BytesIO(data), False
            except serializers.ValidationError:
                raise
            except Exception as e:
                raise serializers.ValidationError(
                    f"Données d'image base64 invalides pour {field_name}: {str(e)}"
                )
        raise serializers.ValidationError(f"Format d'image invalide pour {field_name}")

    def update(self, instance, validated_data):
        avatar_file = None
        avatar_cropped_file = None
        avatar_bytes = None
        old_avatar = instance.avatar
        old_avatar_cropped = instance.avatar_cropped

        if "avatar" in validated_data:
            avatar_file, avatar_bytes, is_url = self._process_image_field(
                "avatar", validated_data
            )
            if validated_data["avatar"] is None or validated_data["avatar"] == "":
                instance.avatar = None
                instance.avatar_cropped = None
            elif avatar_file:
                instance.avatar = avatar_file
                instance.avatar_cropped = None

        if "avatar_cropped" in validated_data:
            avatar_cropped_file, _, is_url = self._process_image_field(
                "avatar_cropped", validated_data
            )
            if (
                validated_data["avatar_cropped"] is None
                or validated_data["avatar_cropped"] == ""
            ):
                instance.avatar_cropped = None
            elif avatar_cropped_file:
                instance.avatar_cropped = avatar_cropped_file

        validated_data.pop("avatar", None)
        validated_data.pop("avatar_cropped", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance._avatar_bytes_for_celery = avatar_bytes if avatar_file else None
        instance.save()

        if "avatar" in validated_data:
            if avatar_file and old_avatar:
                self._delete_file(old_avatar)
            if avatar_file and old_avatar_cropped:
                self._delete_file(old_avatar_cropped)
            if (
                validated_data.get("avatar") is None
                or validated_data.get("avatar") == ""
            ) and old_avatar:
                self._delete_file(old_avatar)
                if old_avatar_cropped:
                    self._delete_file(old_avatar_cropped)

        if "avatar_cropped" in validated_data:
            if avatar_cropped_file and old_avatar_cropped:
                self._delete_file(old_avatar_cropped)
            if (
                validated_data.get("avatar_cropped") is None
                or validated_data.get("avatar_cropped") == ""
            ) and old_avatar_cropped:
                self._delete_file(old_avatar_cropped)

        return instance

    @staticmethod
    def _delete_file(field):
        try:
            if field.path and Path(field.path).exists():
                remove(field.path)
        except (ValueError, FileNotFoundError, OSError):
            pass
        field.delete(save=False)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        request = self.context.get("request")
        for field in ["avatar", "avatar_cropped"]:
            if getattr(instance, field):
                if request:
                    representation[field] = request.build_absolute_uri(
                        getattr(instance, field).url
                    )
                else:
                    representation[field] = getattr(instance, field).url
            else:
                representation[field] = None
        return representation


class UsersListSerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(source="get_absolute_avatar_img")
    avatar_cropped = serializers.CharField(source="get_absolute_avatar_cropped_img")
    gender = serializers.SerializerMethodField()

    @staticmethod
    def get_gender(instance):
        if instance.gender != "":
            return instance.get_gender_display()
        return None

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "first_name",
            "last_name",
            "avatar",
            "avatar_cropped",
            "email",
            "gender",
            "is_active",
            "is_staff",
            "role",
            "date_joined",
            "date_updated",
            "last_login",
            "can_view",
            "can_print",
            "can_create",
            "can_edit",
            "can_delete",
        ]
        read_only_fields = ("date_joined", "date_updated", "last_login")


class UserDetailSerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(source="get_absolute_avatar_img")
    avatar_cropped = serializers.CharField(source="get_absolute_avatar_cropped_img")
    gender = serializers.SerializerMethodField()

    @staticmethod
    def get_gender(instance):
        if instance.gender != "":
            return instance.get_gender_display()
        return None

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "first_name",
            "last_name",
            "avatar",
            "avatar_cropped",
            "email",
            "gender",
            "is_active",
            "is_staff",
            "role",
            "date_joined",
            "date_updated",
            "last_login",
            "can_view",
            "can_print",
            "can_create",
            "can_edit",
            "can_delete",
        ]
        read_only_fields = ("id", "date_joined", "date_updated", "last_login")


class UserPatchSerializer(ProfilePutSerializer):
    class Meta(ProfilePutSerializer.Meta):
        model = CustomUser
        fields = ProfilePutSerializer.Meta.fields + [
            "id",
            "email",
            "is_active",
            "is_staff",
            "role",
            "date_joined",
            "last_login",
            "can_view",
            "can_print",
            "can_create",
            "can_edit",
            "can_delete",
        ]
        read_only_fields = ("id", "email", "date_joined", "last_login")

    def update(self, instance, validated_data):
        return super().update(instance, validated_data)
