import hashlib
import json
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase
from django.urls import reverse

from .models import StickyNote
from .portable_backups import create_data_backup, validate_data_backup, restore_validated_data


class MemoTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="fixture-password-5842")
        self.client.force_login(self.user)

    def test_board_create_edit_conflict_and_delete(self):
        self.assertEqual(self.client.get(reverse("memo_board")).status_code, 200)
        self.assertEqual(self.client.post(reverse("memo_board"), {"content": "기억할 내용\n<script>alert(1)</script>", "color": "rose"}).status_code, 302)
        note = StickyNote.objects.get()
        self.assertContains(self.client.get(reverse("memo_board")), "&lt;script&gt;")
        version = note.updated_at.isoformat()
        edit = reverse("memo_edit", args=[note.pk])
        self.assertEqual(self.client.post(edit, {"content": "수정한 메모", "color": "mint", "version": version}).status_code, 302)
        self.assertEqual(self.client.post(edit, {"content": "오래된 화면", "color": "rose", "version": version}).status_code, 409)
        note.refresh_from_db()
        self.assertEqual(note.content, "수정한 메모")
        delete = reverse("memo_delete", args=[note.pk])
        self.assertEqual(self.client.get(delete).status_code, 200)
        self.assertTrue(StickyNote.objects.exists())
        self.assertEqual(self.client.post(delete, {"version": "invalid"}).status_code, 409)
        self.assertEqual(self.client.post(delete, {"version": version}).status_code, 409)
        self.assertEqual(self.client.post(delete, {"version": note.updated_at.isoformat()}).status_code, 302)
        self.assertFalse(StickyNote.objects.exists())

    def test_validation_authentication_and_csrf(self):
        for content, color in [("  ", "rose"), ("x" * 3001, "rose"), ("memo", "invalid")]:
            self.client.post(reverse("memo_board"), {"content": content, "color": color})
        self.assertFalse(StickyNote.objects.exists())
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(reverse("memo_board"), {"content": "memo", "color": "rose"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("memo_board")).status_code, 302)
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("memo_board")).status_code, 403)

    def test_backup_roundtrip_and_older_backup_preserves_memos(self):
        note = StickyNote.objects.create(content="기억해 둘 메모", color="cream")
        archive, _ = create_data_backup()
        objects, _ = validate_data_backup(archive)
        StickyNote.objects.all().delete()
        restore_validated_data(objects)
        self.assertEqual(StickyNote.objects.get(pk=note.pk).content, note.content)
        with ZipFile(BytesIO(archive)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        rows = [row for row in json.loads(files["data.json"]) if row["model"] != "tasks.stickynote"]
        files["data.json"] = json.dumps(rows).encode()
        manifest = json.loads(files["manifest.json"])
        manifest["format"] = "dugyeon-data-v2"
        manifest["db_schema_version"] = "0010_google_owner_login"
        del manifest["counts"]["tasks.stickynote"]
        manifest["sha256"]["data.json"] = hashlib.sha256(files["data.json"]).hexdigest()
        files["manifest.json"] = json.dumps(manifest).encode()
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as dest:
            for name, content in files.items():
                dest.writestr(name, content)
        objects, _ = validate_data_backup(output.getvalue())
        restore_validated_data(objects)
        self.assertTrue(StickyNote.objects.filter(pk=note.pk).exists())

    def test_archive_and_pin_are_post_only_versioned_and_persistent(self):
        note = StickyNote.objects.create(content="보관할 쪽지")
        move = reverse("memo_move", args=[note.pk])
        version = note.updated_at.isoformat()
        self.assertEqual(self.client.get(move).status_code, 405)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(move, {"action": "archive", "version": version}).status_code, 403)
        self.assertEqual(self.client.post(move, {"action": "archive", "version": version}).status_code, 302)
        note.refresh_from_db()
        self.assertTrue(note.is_archived)
        self.assertNotContains(self.client.get(reverse("memo_board")), note.content)
        self.assertContains(self.client.get(reverse("memo_board") + "?view=archive"), note.content)
        self.client.post(move, {"action": "pin", "version": version})
        note.refresh_from_db()
        self.assertTrue(note.is_archived)
        archive, _ = create_data_backup()
        objects, _ = validate_data_backup(archive)
        restore_validated_data(objects)
        note.refresh_from_db()
        self.assertTrue(note.is_archived)
        self.client.post(move, {"action": "pin", "version": note.updated_at.isoformat()})
        note.refresh_from_db()
        self.assertFalse(note.is_archived)
        self.assertContains(self.client.get(reverse("memo_board")), note.content)

    def test_old_memo_backup_defaults_to_visible_board(self):
        note = StickyNote.objects.create(content="예전 쪽지")
        archive, _ = create_data_backup()
        with ZipFile(BytesIO(archive)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        rows = json.loads(files["data.json"])
        for row in rows:
            if row["model"] == "tasks.stickynote":
                del row["fields"]["is_archived"]
        files["data.json"] = json.dumps(rows).encode()
        manifest = json.loads(files["manifest.json"])
        manifest["db_schema_version"] = "0011_stickynote"
        manifest["sha256"]["data.json"] = hashlib.sha256(files["data.json"]).hexdigest()
        files["manifest.json"] = json.dumps(manifest).encode()
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as dest:
            for name, content in files.items():
                dest.writestr(name, content)
        objects, _ = validate_data_backup(output.getvalue())
        restore_validated_data(objects)
        self.assertFalse(StickyNote.objects.get(pk=note.pk).is_archived)
