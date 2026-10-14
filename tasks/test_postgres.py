import secrets as _review_secrets
from urllib.parse import quote as _review_quote
_REVIEW_PASSWORD_1 = _review_secrets.token_urlsafe(32) + "@?"
_REVIEW_PASSWORD_2 = _review_secrets.token_urlsafe(32) + "@?"

from io import BytesIO, StringIO
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
from unittest import mock
import zipfile
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, CommandError
from django.db import connections, IntegrityError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from config.database import postgres_database
from .backups import BackupRestoreError, BackupValidationError, restore_database_archive
from .models import BackupLog, BackupReminder, Category, DailyRecord, Task, TaskStatusHistory
from .portable_backups import create_data_backup, restore_validated_data, validate_data_backup

class DatabaseURLTests(SimpleTestCase):

    def test_sqlite_restore_refuses_postgres_without_reading_upload(self):
        with mock.patch('tasks.backups.connections', {'default': mock.Mock(vendor='postgresql')}):
            with self.assertRaises(BackupRestoreError):
                restore_database_archive(b'invalid archive')

    def test_encoded_credentials_and_pooling_options(self):
        result = postgres_database('postgresql://fixture:' + _review_quote(_REVIEW_PASSWORD_1, safe='') + '@db.example.test/app?sslmode=require')
        self.assertEqual(result['PASSWORD'], _REVIEW_PASSWORD_1)
        self.assertEqual(result['OPTIONS']['sslmode'], 'require')
        self.assertEqual(result['CONN_MAX_AGE'], 0)
        self.assertTrue(result['DISABLE_SERVER_SIDE_CURSORS'])

    def test_invalid_urls_and_insecure_production_options_are_rejected(self):
        for url in ['sqlite:///fixture', 'postgresql://db', 'postgresql://db/app?sslmode=disable', 'postgresql://db:invalid/app', 'postgresql://db/app?options=unexpected', 'postgresql://db/app?sslmode=require&sslmode=disable']:
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured):
                postgres_database(url)

    def test_exception_does_not_include_credentials(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            postgres_database('postgresql://fixture:' + _review_quote(_REVIEW_PASSWORD_2, safe='') + '@db:bad/app')
        self.assertNotIn(_REVIEW_PASSWORD_2, str(caught.exception))

class PortableBackupTests(TransactionTestCase):

    def setUp(self):
        self.password = secrets.token_urlsafe(32)
        self.user = get_user_model().objects.create_user(username='portable-fixture', password=self.password)
        self.category = Category.objects.create(name='Fixture category')
        self.task = Task.objects.create(category=self.category, task_date=timezone.localdate(), title='Fixture task')
        TaskStatusHistory.objects.create(task=self.task, from_status=Task.Status.PENDING, to_status=Task.Status.DONE)
        self.record = DailyRecord.objects.create(record_date=timezone.localdate(), today_story='Fixture record')

    def rewrite(self, content, transform):
        with zipfile.ZipFile(BytesIO(content)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        data = json.loads(files['data.json'])
        transform(data)
        files['data.json'] = json.dumps(data).encode()
        manifest = json.loads(files['manifest.json'])
        manifest['sha256']['data.json'] = hashlib.sha256(files['data.json']).hexdigest()
        files['manifest.json'] = json.dumps(manifest).encode()
        output = BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        return output.getvalue()

    def test_backup_roundtrip_preserves_accounts_and_history(self):
        archive, _ = create_data_backup()
        objects, manifest = validate_data_backup(archive)
        Task.objects.filter(pk=self.task.pk).update(title='Changed fixture')
        restore_validated_data(objects)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Fixture task')
        self.assertEqual(TaskStatusHistory.objects.count(), 1)
        self.assertEqual(DailyRecord.objects.count(), 1)
        self.assertTrue(get_user_model().objects.get(pk=self.user.pk).check_password(self.password))
        with zipfile.ZipFile(BytesIO(archive)) as backup:
            self.assertNotIn(b'auth.user', backup.read('data.json'))

    def test_unknown_model_cannot_change_accounts(self):
        archive, _ = create_data_backup()
        altered = self.rewrite(archive, lambda data: data[0].update(model='auth.user'))
        with self.assertRaises(BackupValidationError):
            validate_data_backup(altered)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_orphan_and_oversized_values_rejected_before_restore(self):
        archive, _ = create_data_backup()

        def orphan(data):
            for row in data:
                if row['model'] == 'tasks.task':
                    row['fields']['category'] = '00000000-0000-0000-0000-000000000000'

        def oversized(data):
            for row in data:
                if row['model'] == 'tasks.task':
                    row['fields']['details'] = 'x' * 2001
        for transform in (orphan, oversized):
            with self.assertRaises(BackupValidationError):
                validate_data_backup(self.rewrite(archive, transform))
        self.assertEqual(Task.objects.count(), 1)

    def test_restore_failure_rolls_back_current_data(self):
        archive, _ = create_data_backup()
        objects, _ = validate_data_backup(archive)
        with mock.patch.object(objects[0], 'save', side_effect=IntegrityError('fixture failure')):
            with self.assertRaises(IntegrityError):
                restore_validated_data(objects)
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_duplicate_uuid_spelling_is_rejected(self):
        archive, _ = create_data_backup()

        def duplicate(data):
            row = dict(data[0])
            row['pk'] = row['pk'].upper()
            data.append(row)
        altered = self.rewrite(archive, duplicate)
        with zipfile.ZipFile(BytesIO(altered)) as backup:
            files = {name: backup.read(name) for name in backup.namelist()}
        manifest = json.loads(files['manifest.json'])
        manifest['counts']['tasks.category'] += 1
        files['manifest.json'] = json.dumps(manifest).encode()
        output = BytesIO()
        with zipfile.ZipFile(output, 'w') as backup:
            for name, content in files.items():
                backup.writestr(name, content)
        with self.assertRaises(BackupValidationError):
            validate_data_backup(output.getvalue())

    def test_postgres_ui_download_and_web_restore_rejection(self):
        if connections['default'].vendor != 'postgresql':
            self.skipTest('PostgreSQL integration test')
        self.client.force_login(self.user)
        self.assertContains(self.client.get('/settings/backups/'), 'ZIP')
        response = self.client.post('/settings/backups/create/', {'password': self.password})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('no-store', response['Cache-Control'])
        objects, manifest = validate_data_backup(response.content)
        self.assertEqual(manifest['format'], 'dugyeon-data-v2')
        self.assertFalse(manifest['photo_originals_included'])
        self.assertTrue(any(isinstance(item.object, DailyRecord) for item in objects))
        log = BackupLog.objects.get(backup_type=BackupLog.BackupType.MANUAL)
        self.assertEqual(log.sha256, hashlib.sha256(response.content).hexdigest())
        self.assertFalse(BackupReminder.objects.filter(user=self.user, last_confirmed_at__isnull=False).exists())
        confirmation = self.client.post('/settings/backups/reminder/', {
            'action': 'confirm', 'kind': 'data', 'saved_schedule': 'yes',
        })
        self.assertEqual(confirmation.status_code, 302)
        reminder = BackupReminder.objects.get(user=self.user)
        self.assertIsNotNone(reminder.last_confirmed_at)
        self.assertIsNone(reminder.last_full_photos_confirmed_at)
        self.assertEqual(Task.objects.get(pk=self.task.pk).title, 'Fixture task')
        self.assertEqual(self.client.post('/settings/backups/restore/', {}).status_code, 400)

    def test_offline_restore_requires_confirmation_and_saves_safety_backup(self):
        if connections['default'].vendor != 'postgresql':
            self.skipTest('PostgreSQL integration test')
        archive, _ = create_data_backup()
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_DIR=Path(directory) / 'backups'):
            path = Path(directory) / 'fixture.zip'
            path.write_bytes(archive)
            with self.assertRaises(CommandError):
                call_command('restore_data_backup', str(path), stdout=StringIO())
            Task.objects.filter(pk=self.task.pk).update(title='Before restore')
            call_command('restore_data_backup', str(path), confirm=True, confirm_writes_stopped=True, stdout=StringIO())
            self.assertEqual(Task.objects.get(pk=self.task.pk).title, 'Fixture task')
            safety = next((Path(directory) / 'backups').glob('schedule-pre-restore-*.zip'))
            objects, _ = validate_data_backup(safety.read_bytes())
            self.assertEqual(next((item.object.title for item in objects if isinstance(item.object, Task))), 'Before restore')
