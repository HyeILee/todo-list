from django.conf import settings
from django.contrib.auth import get_user_model


def is_owner(user):
    """This personal app has one shared dataset, so other accounts cannot use it."""
    if not settings.APP_SINGLE_USER:
        return True
    if settings.APP_OWNER_USERNAME:
        return user.get_username() == settings.APP_OWNER_USERNAME
    owner_id = get_user_model().objects.filter(is_active=True).order_by("date_joined", "pk").values_list("pk", flat=True).first()
    return user.pk == owner_id
