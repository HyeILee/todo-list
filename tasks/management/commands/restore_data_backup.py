"""Explicit offline restore with a mandatory private safety backup."""
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, DatabaseError
from tasks.backups import BackupValidationError, MAX_ARCHIVE_SIZE
from tasks.portable_backups import create_data_backup, restore_validated_data, validate_data_backup, write_private_backup


class Command(BaseCommand):
    help = "Restore a PostgreSQL schedule ZIP offline, preserving accounts and saving a safety backup."

    def add_arguments(self, parser):
        parser.add_argument("archive", type=Path)
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--confirm-writes-stopped", action="store_true")

    def handle(self, *args, **options):
        if settings.IS_VERCEL or connections["default"].vendor != "postgresql":
            raise CommandError("Run on a trusted PC connected to PostgreSQL, outside Vercel.")
        if not options["confirm"] or not options["confirm_writes_stopped"]:
            raise CommandError("Stop app writes, then pass --confirm --confirm-writes-stopped.")
        archive = options["archive"]
        try:
            if archive.stat().st_size > MAX_ARCHIVE_SIZE:
                raise BackupValidationError("Backup exceeds the supported size.")
            objects, manifest = validate_data_backup(archive.read_bytes())
            snapshot, _ = create_data_backup()
            safety = write_private_backup(snapshot, settings.BACKUP_DIR, "schedule-pre-restore")
        except (OSError, ValueError, BackupValidationError):
            raise CommandError("Archive validation or safety backup failed; current data unchanged.") from None
        self.stdout.write(f"Safety backup saved: {safety}")
        try:
            restore_validated_data(objects)
        except (DatabaseError, ValueError):
            raise CommandError("Restore rolled back; current data unchanged. Safety backup retained.") from None
        self.stdout.write(self.style.SUCCESS("Schedule data restored; existing accounts preserved."))
