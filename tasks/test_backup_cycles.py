from datetime import datetime, timedelta
from datetime import timezone as utc_timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import BackupReminder, DailyPhoto
from .reminders import reminder_context, six_months_after


class BackupCycleTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner", password="Fixture-password-4729")
        self.client.force_login(self.owner)
        self.now = timezone.now()
        self.preference = BackupReminder.objects.create(user=self.owner, last_confirmed_at=self.now)

    def photo(self, position=0):
        return DailyPhoto.objects.create(record_date=timezone.localdate(), blob_url="https://fixture.private.blob.vercel-storage.com/photo.jpg",
                                        blob_pathname=f"photos/{position}.jpg", original_name=f"{position}.jpg", content_type="image/jpeg", size_bytes=5, position=position)

    def confirm_photo(self, scope, token, saved="yes"):
        return self.client.post(reverse("backup_reminder_update"), {"action": "confirm", "kind": scope, "saved_photos": saved, "confirmation_token": token})

    def test_warning_boundaries_and_each_stage_popup_only_once_across_devices(self):
        other = Client()
        other.force_login(self.owner)
        for days, severity in ((6, "ready"), (7, "due"), (13, "due"), (14, "warning"), (29, "warning"), (30, "critical")):
            with self.subTest(days=days), mock.patch("tasks.reminders.timezone.now", return_value=self.now + timedelta(days=days)):
                self.client.force_login(self.owner)
                other.force_login(self.owner)
                context = reminder_context(self.owner)
                self.assertEqual(context["backup_cycles"][0]["severity"], severity)
                page = self.client.get("/")
                self.assertEqual(page.context["backup_reminder_show_popup"], days in {7, 14, 30})
                self.assertFalse(other.get("/").context["backup_reminder_show_popup"])

    def test_photo_cycles_use_calendar_months_and_skip_new_alert_without_new_photos(self):
        self.photo()
        old = self.now - timedelta(days=31)
        self.preference.last_new_photos_confirmed_at = old
        self.preference.last_full_photos_confirmed_at = self.now
        self.preference.photos_covered_until = self.now - timedelta(days=1)
        self.preference.save()
        cycles = reminder_context(self.owner)["backup_cycles"]
        self.assertTrue(cycles[1]["due"])
        self.assertFalse(cycles[2]["due"])
        self.preference.photos_covered_until = timezone.now()
        self.preference.save()
        self.assertFalse(reminder_context(self.owner)["backup_cycles"][1]["due"])
        august = datetime(2025, 8, 31, 1, 0, tzinfo=utc_timezone.utc)
        self.assertEqual(six_months_after(august).date().isoformat(), "2026-02-28")

    def test_full_confirmation_does_not_skip_photos_added_while_downloading(self):
        first = self.photo()
        manifest = self.client.get(reverse("photo_manifest"), {"scope": "full"}).json()
        later = self.photo(1)
        self.confirm_photo("full", manifest["confirmation_token"])
        self.preference.refresh_from_db()
        self.assertIsNotNone(self.preference.last_new_photos_confirmed_at)
        self.assertIsNotNone(self.preference.last_full_photos_confirmed_at)
        self.assertEqual(self.preference.last_confirmed_at, self.now)
        pending = self.client.get(reverse("photo_manifest"), {"scope": "new"}).json()
        self.assertEqual([item["url"] for item in pending["files"]], [reverse("photo_content", args=[later.pk])])
        self.assertTrue(DailyPhoto.objects.filter(pk=first.pk).exists())

    def test_new_photo_confirmation_keeps_full_cycle_and_rejects_replay(self):
        self.photo()
        token = self.client.get(reverse("photo_manifest"), {"scope": "new"}).json()["confirmation_token"]
        self.confirm_photo("new", token, saved="")
        self.preference.refresh_from_db()
        self.assertIsNone(self.preference.last_new_photos_confirmed_at)
        self.confirm_photo("new", token)
        self.preference.refresh_from_db()
        confirmed = self.preference.last_new_photos_confirmed_at
        self.assertIsNone(self.preference.last_full_photos_confirmed_at)
        self.confirm_photo("new", token)
        self.preference.refresh_from_db()
        self.assertEqual(self.preference.last_new_photos_confirmed_at, confirmed)

    def test_photo_receipt_is_bound_to_kind_account_and_signature(self):
        self.photo()
        token = self.client.get(reverse("photo_manifest"), {"scope": "new"}).json()["confirmation_token"]
        for scope, receipt in (("full", token), ("new", token + "invalid"), ("new", "")):
            self.confirm_photo(scope, receipt)
        self.preference.refresh_from_db()
        self.assertIsNone(self.preference.photos_covered_until)

    def test_manifest_creation_alone_does_not_confirm_storage_and_has_no_blob_urls(self):
        self.photo()
        response = self.client.get(reverse("photo_manifest"), {"scope": "full"})
        self.assertNotContains(response, "vercel-storage.com")
        self.preference.refresh_from_db()
        self.assertIsNone(self.preference.last_full_photos_confirmed_at)
        self.assertEqual(self.client.get(reverse("photo_manifest"), {"scope": "wrong"}).status_code, 400)
