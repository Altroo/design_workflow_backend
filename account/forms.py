from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from account.models import CustomUser


class CustomAuthShopCreationForm(UserCreationForm):

    class Meta(UserCreationForm):
        model = CustomUser
        fields = ("email",)


class CustomAuthShopChangeForm(UserChangeForm):

    class Meta:
        model = CustomUser
        fields = ("email",)
