from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .access import is_owner


class OwnerAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not is_owner(user):
            raise ValidationError(self.error_messages["invalid_login"], code="invalid_login", params={"username": self.username_field.verbose_name})
