from datetime import timedelta
from io import BytesIO
import hashlib
import json
import zipfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import BackupLog, BackupReminder, Category, DailyPhoto, DailyRecord, Task
from .portable_backups import create_data_backup, validate_data_backup, restore_validated_data


class SecurityReminderTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner", password="Long-owner-pass-2847")
        self.client.force_login(self.owner)

    def test_other_account_cannot_read_or_download_owner_records(self):
        other = get_user_model().objects.create_user(username="other", password="Long-other-pass-2847")
        DailyRecord.objects.create(record_date=timezone.localdate(), today_story="Private fixture")
        other_client = Client()
        other_client.force_login(other)
        for url in ("/", "/records/", "/exports/records.csv", "/photos/manifest/", "/settings/backups/"):
            response = other_client.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(b"Private fixture", response.content)
        other_client.logout()
        response = other_client.post(reverse("login"), {"username": "other", "password": "Long-other-pass-2847"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", other_client.session)

    def test_reminders_are_due_after_interval_and_snooze_is_shared_between_devices(self):
        preference = BackupReminder.objects.create(user=self.owner, last_confirmed_at=timezone.now() - timedelta(days=6))
        self.assertFalse(self.client.get("/").context["backup_reminder_due"])
        preference.last_confirmed_at = timezone.now() - timedelta(days=8)
        preference.save()
        self.assertTrue(self.client.get("/").context["backup_reminder_show_popup"])
        response = self.client.post(reverse("backup_reminder_update"), {"action": "snooze"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json(), {"ok": True})
        another_device = Client()
        another_device.force_login(self.owner)
        self.assertFalse(another_device.get("/").context["backup_reminder_due"])
        preference.refresh_from_db()
        preference.snoozed_until = timezone.now() - timedelta(seconds=1)
        preference.save()
        self.assertTrue(another_device.get("/").context["backup_reminder_due"])

    def test_settings_reject_invalid_interval_without_changing_preferences(self):
        url = reverse("backup_reminder_update")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"action": "interval", "interval_days": "0"}).status_code, 400)
        self.assertFalse(BackupReminder.objects.exists())
        self.assertEqual(self.client.post(url, {"action": "interval", "interval_days": "14"}).status_code, 302)
        self.assertEqual(BackupReminder.objects.get(user=self.owner).interval_days, 14)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        self.assertEqual(protected.post(url, {"action": "snooze"}).status_code, 403)

    def test_snooze_with_page_csrf_token_lasts_exactly_one_day(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        page = protected.get("/")
        self.assertContains(page, 'backup-reminder.js?v=2')
        token = protected.cookies["csrftoken"].value
        now = timezone.now()
        with mock.patch("tasks.views.timezone.now", return_value=now):
            response = protected.post(reverse("backup_reminder_update"), {
                "action": "snooze", "csrfmiddlewaretoken": token,
            }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json(), {"ok": True})
        preference = BackupReminder.objects.get(user=self.owner)
        self.assertEqual(preference.snoozed_until, now + timedelta(days=1))
        with mock.patch("tasks.reminders.timezone.now", return_value=now + timedelta(hours=23)):
            protected.force_login(self.owner)
            self.assertFalse(protected.get("/").context["backup_reminder_due"])
        with mock.patch("tasks.reminders.timezone.now", return_value=now + timedelta(days=1, seconds=1)):
            protected.force_login(self.owner)
            self.assertTrue(protected.get("/").context["backup_reminder_show_popup"])

    def test_backup_workflow_is_quiet_without_recording_completion(self):
        self.assertTrue(self.client.get("/").context["backup_reminder_show_popup"])
        self.client.get(reverse("backup_page"))
        for url in ("/", "/photos/", "/settings/"):
            response = self.client.get(url)
            self.assertTrue(response.context["backup_reminder_due"])
            self.assertFalse(response.context["backup_reminder_show_popup"])
            self.assertFalse(response.context["backup_reminder_show_notice"])
            self.assertNotContains(response, 'id="backup-reminder-dialog"')
        self.assertIsNone(BackupReminder.objects.get(user=self.owner).last_confirmed_at)
        session = self.client.session
        session["backup_reminder_quiet_until"] = (timezone.now() - timedelta(seconds=1)).timestamp()
        session.save()
        self.assertFalse(self.client.get("/").context["backup_reminder_show_popup"])

    def test_popup_only_appears_on_full_dashboard_visits(self):
        self.assertTrue(self.client.get("/").context["backup_reminder_show_popup"])
        self.assertFalse(self.client.get(reverse("day", args=[timezone.localdate()])).context["backup_reminder_show_popup"])
        for url in ("/photos/", "/settings/", "/records/"):
            response = self.client.get(url)
            self.assertTrue(response.context["backup_reminder_due"])
            self.assertFalse(response.context["backup_reminder_show_popup"])
        for kwargs in ({"data": {"fragment": "1"}}, {"HTTP_HX_REQUEST": "true"}):
            response = self.client.get("/", **kwargs)
            self.assertNotContains(response, 'id="backup-reminder-dialog"')

    def test_data_confirmation_requires_own_download_and_keeps_photo_cycles_separate(self):
        url = reverse("backup_reminder_update")
        self.add_backup_log()  # A global log alone cannot confirm this account's download.
        self.client.post(url, {"action": "confirm", "kind": "data", "saved_schedule": "yes"})
        self.assertIsNone(BackupReminder.objects.get(user=self.owner).last_confirmed_at)
        log = self.add_backup_log()
        session = self.client.session
        session["backup_data_download"] = str(log.pk)
        session.save()
        self.add_photo()
        self.client.post(url, {"action": "confirm", "kind": "data", "saved_schedule": "yes"})
        preference = BackupReminder.objects.get(user=self.owner)
        self.assertIsNotNone(preference.last_confirmed_at)
        self.assertIsNone(preference.last_full_photos_confirmed_at)
        self.assertIsNone(preference.last_new_photos_confirmed_at)

    def test_generated_backup_does_not_reset_reminder(self):
        self.add_backup_log()
        self.assertTrue(self.client.get("/").context["backup_reminder_due"])
        response = self.client.get(reverse("backup_page"))
        self.assertFalse(response.context["backup_reminder_show_popup"])

    def add_backup_log(self):
        return BackupLog.objects.create(backup_type="MANUAL", status="SUCCESS", started_at=timezone.now(), completed_at=timezone.now(), file_name="fixture.zip", sha256="a" * 64)

    def add_photo(self):
        return DailyPhoto.objects.create(record_date=timezone.localdate(), blob_url="https://store.private.blob.vercel-storage.com/photos/fixture.jpg", blob_pathname="photos/fixture.jpg", original_name="fixture.jpg", content_type="image/jpeg", size_bytes=53, position=0)

    def test_authenticated_responses_and_login_are_not_cacheable(self):
        for url in ("/settings/", "/photos/manifest/", "/settings/backups/", "/accounts/login/"):
            self.assertIn("no-store", self.client.get(url)["Cache-Control"])

    @mock.patch("tasks.backups.ensure_daily_backup_before_change")
    def test_normal_saves_do_not_generate_automatic_backups(self, automatic):
        category = Category.objects.create(name="Fixture")
        self.client.post(reverse("task_create"), {"category": category.pk, "title": "Preserved save", "task_date": "2026-10-05", "details": ""})
        self.assertTrue(Task.objects.filter(title="Preserved save").exists())
        automatic.assert_not_called()


class PortablePhotoBackupTests(TransactionTestCase):
    def setUp(self):
        self.photo = DailyPhoto.objects.create(record_date=timezone.localdate(), blob_url="https://store.private.blob.vercel-storage.com/photos/fixture.jpg", blob_pathname="photos/fixture.jpg", original_name="fixture.jpg", content_type="image/jpeg", size_bytes=53, position=0)

    def test_photo_metadata_round_trip_and_account_reminders_are_preserved(self):
        owner = get_user_model().objects.create_user(username="fixture", password="Long-owner-pass-2847")
        reminder = BackupReminder.objects.create(user=owner, interval_days=14)
        archive, _ = create_data_backup()
        objects, manifest = validate_data_backup(archive)
        self.assertFalse(manifest["photo_originals_included"])
        DailyPhoto.objects.all().delete()
        restore_validated_data(objects)
        self.assertTrue(DailyPhoto.objects.filter(pk=self.photo.pk).exists())
        self.assertTrue(BackupReminder.objects.filter(pk=reminder.pk, interval_days=14).exists())
        self.assertTrue(get_user_model().objects.filter(pk=owner.pk).exists())

    def test_previous_portable_backup_remains_usable_and_does_not_delete_photos(self):
        archive, _ = create_data_backup()
        with zipfile.ZipFile(BytesIO(archive)) as original:
            files = {name: original.read(name) for name in original.namelist()}
        data = [row for row in json.loads(files["data.json"]) if row["model"] != "tasks.dailyphoto"]
        files["data.json"] = json.dumps(data).encode()
        manifest = json.loads(files["manifest.json"])
        manifest["format"] = "dugyeon-data-v1"
        manifest["db_schema_version"] = "0006_dailyphoto_category"
        manifest["counts"].pop("tasks.dailyphoto")
        manifest["sha256"]["data.json"] = hashlib.sha256(files["data.json"]).hexdigest()
        files["manifest.json"] = json.dumps(manifest).encode()
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as rewritten:
            for name, content in files.items():
                rewritten.writestr(name, content)
        objects, _ = validate_data_backup(buffer.getvalue())
        restore_validated_data(objects)
        self.assertTrue(DailyPhoto.objects.filter(pk=self.photo.pk).exists())
