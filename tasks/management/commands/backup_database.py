"""Create a consistent SQLite snapshot without copying a live database file."""
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone


class Command(BaseCommand):
    help = "Back up the configured SQLite database using SQLite's backup API."

    def handle(self, *args, **options):
        database = connections["default"]
        if database.vendor == "postgresql":
            if settings.IS_VERCEL:
                raise CommandError("Download backups from the app; run filesystem backup commands on a trusted PC.")
            from tasks.portable_backups import create_data_backup, write_private_backup
            content, _ = create_data_backup()
            target = write_private_backup(content, settings.BACKUP_DIR, "schedule-data")
            self.stdout.write(self.style.SUCCESS(f"Schedule data backup created: {target}"))
            return
        source = Path(database.settings_dict["NAME"])
        if database.vendor != "sqlite" or not source.is_file():
            raise CommandError("An existing SQLite database is required.")
        directory = Path(settings.BACKUP_DIR)
        # Backups must never be served as static files.
        for static in (settings.BASE_DIR / "static", settings.STATIC_ROOT):
            static = Path(static).resolve()
            if directory.resolve() == static or static in directory.resolve().parents:
                raise CommandError("BACKUP_DIR must be outside static directories.")
        directory.mkdir(parents=True, exist_ok=True)
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S-%f")
        target = directory / f"database-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        try:
            # Read-only source prevents accidental creation or changes to the source DB.
            with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin:
                with closing(sqlite3.connect(target)) as destination:
                    origin.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise sqlite3.DatabaseError("Snapshot integrity check failed")
        except (sqlite3.Error, OSError) as error:
            raise CommandError(f"Backup failed ({type(error).__name__}); source unchanged.") from None
        self.stdout.write(self.style.SUCCESS(f"Database backup created: {target}"))
