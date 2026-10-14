import hashlib
import sqlite3
import tempfile
from pathlib import Path
from io import StringIO
from contextlib import closing
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase, override_settings


class DatabaseBackupCommandTests(SimpleTestCase):
    def test_snapshot_matches_source_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE fixture (value INTEGER)")
                connection.execute("INSERT INTO fixture VALUES (42)")
                connection.commit()
            before = hashlib.sha256(source.read_bytes()).digest()
            database = mock.Mock(vendor="sqlite", settings_dict={"NAME": source})
            with override_settings(BACKUP_DIR=Path(directory) / "backups"):
                with mock.patch("tasks.management.commands.backup_database.connections", {"default": database}):
                    call_command("backup_database", stdout=StringIO())
            snapshots = list((Path(directory) / "backups").glob("database-*.sqlite3"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            with closing(sqlite3.connect(snapshots[0])) as backup:
                self.assertEqual(backup.execute("SELECT value FROM fixture").fetchone(), (42,))

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "missing.sqlite3"
            database = mock.Mock(vendor="sqlite", settings_dict={"NAME": source})
            with mock.patch("tasks.management.commands.backup_database.connections", {"default": database}):
                with self.assertRaises(CommandError):
                    call_command("backup_database", stdout=StringIO())
            self.assertFalse(source.exists())

    def test_public_backup_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            source.touch()
            database = mock.Mock(vendor="sqlite", settings_dict={"NAME": source})
            with override_settings(BACKUP_DIR=settings.STATIC_ROOT):
                with mock.patch("tasks.management.commands.backup_database.connections", {"default": database}):
                    with self.assertRaises(CommandError):
                        call_command("backup_database", stdout=StringIO())


class BootstrapInitialUserTests(TestCase):
    def test_no_credentials_is_a_safe_noop(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            call_command("bootstrap_initial_user", verbosity=0)
        self.assertFalse(get_user_model().objects.exists())

    def test_partial_credentials_are_rejected(self):
        with mock.patch.dict("os.environ", {"DJANGO_BOOTSTRAP_USERNAME": "owner"}, clear=True):
            with self.assertRaises(CommandError):
                call_command("bootstrap_initial_user", verbosity=0)
        self.assertFalse(get_user_model().objects.exists())

    def test_creates_only_the_first_user(self):
        environment = {
            "DJANGO_BOOTSTRAP_USERNAME": "owner",
            "DJANGO_BOOTSTRAP_PASSWORD": "A-unique-deployment-password-4829",
        }
        with mock.patch.dict("os.environ", environment, clear=True):
            call_command("bootstrap_initial_user", verbosity=0)
            call_command("bootstrap_initial_user", verbosity=0)
        users = get_user_model().objects.all()
        self.assertEqual(users.count(), 1)
        self.assertTrue(users.get().check_password(environment["DJANGO_BOOTSTRAP_PASSWORD"]))
