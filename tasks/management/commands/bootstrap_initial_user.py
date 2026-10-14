"""Create the first application user from one-time deployment secrets."""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create the first user only when the database has no users."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_BOOTSTRAP_USERNAME", "").strip()
        password = os.environ.get("DJANGO_BOOTSTRAP_PASSWORD", "")

        if not username and not password:
            self.stdout.write("Initial user bootstrap skipped; no one-time credentials configured.")
            return
        if not username or not password:
            raise CommandError("Both DJANGO_BOOTSTRAP_USERNAME and DJANGO_BOOTSTRAP_PASSWORD are required.")

        user_model = get_user_model()
        with transaction.atomic():
            if user_model.objects.exists():
                self.stdout.write("Initial user bootstrap skipped; a user already exists.")
                return
            user = user_model(username=username)
            try:
                validate_password(password, user=user)
            except ValidationError as error:
                raise CommandError("The bootstrap password does not satisfy the password policy.") from error
            user.set_password(password)
            user.save()

        self.stdout.write(self.style.SUCCESS("Initial application user created."))
