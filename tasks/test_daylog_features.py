from datetime import date
from io import BytesIO, StringIO
from unittest import mock
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Category, DailyPhoto, DailyRecord, Holiday, Task
from .holidays import HolidaySyncError, parse_kasi_holidays


class DayLogFeatureTests(TestCase):
    def setUp(self):
        backup = mock.patch("tasks.backups.ensure_daily_backup_before_change", return_value=True)
        backup.start()
        self.addCleanup(backup.stop)
        self.user = get_user_model().objects.create_user(username="daylog-owner", password="Strong-test-pass-462!")

    def test_login_copy_is_updated(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "PERSONAL DAY LOG")
        self.assertContains(response, "협소한 내 마음에")
        self.assertContains(response, "옮겨 담으려던 것은")
        self.assertContains(response, "- 김이듬, 〈달에서 더 멀리〉")
        self.assertContains(response, "기록은 안전한 클라우드 데이터베이스에 보관됩니다.")
        self.assertNotContains(response, "PERSONAL WORK LOG")

    def test_navigation_and_record_heading_are_updated(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("day", args=["2026-10-02"]))
        for label in ("한 주와 오늘", "전체 퀘스트", "일기장", "사진첩", "이날의 기록"):
            self.assertContains(response, label)
        self.assertContains(self.client.get(reverse("task_list")), "<h1>전체 퀘스트</h1>", html=True)
        self.assertContains(self.client.get(reverse("record_list")), "<h1>일기장</h1>", html=True)

    def test_week_summary_counts_every_non_done_task_as_pending(self):
        category = Category.objects.create(name="개인")
        statuses = [Task.Status.DONE, Task.Status.PENDING, Task.Status.NOT_DONE, Task.Status.PARTIAL]
        for index, status in enumerate(statuses):
            Task.objects.create(task_date=date(2026, 10, 2), category=category, title=f"퀘스트 {index}", status=status)
        self.client.force_login(self.user)
        response = self.client.get(reverse("day", args=["2026-10-02"]))
        summary = next(item for item in response.context["week"] if item["date"] == date(2026, 10, 2))
        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["done_count"], 1)
        self.assertEqual(summary["pending_count"], 3)
        self.assertContains(response, "총 4건")
        self.assertContains(response, "(완료 1건, 미결 3건)")

    def test_task_dialog_omits_save_and_add_button(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("task_create"), {"date": "2026-10-02", "fragment": "1"})
        self.assertNotContains(response, "저장 후 추가")

    def test_dashboard_calls_soft_delete_delete(self):
        category = Category.objects.create(name="개인")
        Task.objects.create(task_date=date(2026, 10, 2), category=category, title="지울 퀘스트")
        self.client.force_login(self.user)
        response = self.client.get(reverse("day", args=["2026-10-02"]))
        self.assertContains(response, ">삭제</button>")
        self.assertNotContains(response, ">보관</button>")

    def test_saturday_sunday_and_holiday_classes(self):
        Holiday.objects.create(holiday_date=date(2026, 10, 3), name="개천절")
        self.client.force_login(self.user)
        response = self.client.get(reverse("day", args=["2026-10-02"]))
        by_date = {item["date"]: item for item in response.context["week"]}
        self.assertEqual(by_date[date(2026, 10, 3)]["calendar_class"], "holiday")
        self.assertEqual(by_date[date(2026, 10, 3)]["holiday_name"], "개천절")
        self.assertEqual(by_date[date(2026, 10, 4)]["calendar_class"], "sunday")
        Holiday.objects.filter(holiday_date=date(2026, 10, 3)).delete()
        response = self.client.get(reverse("day", args=["2026-10-02"]))
        by_date = {item["date"]: item for item in response.context["week"]}
        self.assertEqual(by_date[date(2026, 10, 3)]["calendar_class"], "saturday")

    def test_all_record_fields_accept_2000_characters(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("record_save", args=["2026-10-02"]), {
            "today_story": "a" * 2000,
            "memorable_sentence": "b" * 2000,
            "stayed_with_me": "c" * 2000,
            "expected_updated_at": "",
        })
        self.assertEqual(response.status_code, 302)
        record = DailyRecord.objects.get(record_date=date(2026, 10, 2))
        self.assertEqual(len(record.today_story), 2000)
        self.assertEqual(len(record.memorable_sentence), 2000)
        self.assertEqual(len(record.stayed_with_me), 2000)

    def test_bold_notes_save_and_render_with_html_escaped(self):
        self.client.force_login(self.user)
        content = '안녕 **친구**\n**<img src=x onerror=alert(1)>**'
        response = self.client.post(reverse("record_save", args=["2026-10-07"]), {
            "today_story": content, "memorable_sentence": "**기억**", "stayed_with_me": "곁에", "expected_updated_at": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DailyRecord.objects.get().today_story, content)
        listing = self.client.get(reverse("record_list"))
        self.assertContains(listing, "<strong>친구</strong><br>")
        self.assertContains(listing, "<strong>&lt;img src=x onerror=alert(1)&gt;</strong>")
        self.assertNotContains(listing, "<img src=x")

    def test_colored_sized_underlined_notes_save_and_render_safely(self):
        self.client.force_login(self.user)
        content = '[color=blue][size=large][u]**기억 <img src=x>**[/u][/size][/color]'
        response = self.client.post(reverse("record_save", args=["2026-10-07"]), {
            "today_story": content, "memorable_sentence": "", "stayed_with_me": "", "expected_updated_at": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DailyRecord.objects.get().today_story, content)
        listing = self.client.get(reverse("record_list"))
        self.assertContains(listing, '<span class="note-color-blue"><span class="note-size-large"><span class="note-underline"><strong>기억 &lt;img src=x&gt;</strong></span></span></span>')
        self.assertNotContains(listing, '<img src=x>')

    def test_nested_and_unknown_styles_never_accept_arbitrary_html_or_css(self):
        from .templatetags.record_text import bold_note
        self.assertEqual(bold_note('[color=blue]a[color=rose]b[/color]c[/color]'), '<span class="note-color-blue">a<span class="note-color-rose">b</span>c</span>')
        self.assertEqual(bold_note('[color=evil]문자[/color]<script>x</script>'), '[color=evil]문자[/color]&lt;script&gt;x&lt;/script&gt;')
        self.assertEqual(bold_note('[u]미완성'), '[u]미완성')
        deep = '[u]' * 20 + '<script>x</script>' + '[/u]' * 20
        self.assertNotIn('<script>', bold_note(deep))
        self.assertNotIn('class=', bold_note(deep))

    def test_unclosed_bold_and_html_are_literal_text(self):
        from .templatetags.record_text import bold_note
        self.assertEqual(bold_note("그대로 **남김"), "그대로 **남김")
        self.assertEqual(bold_note("**한 글**\n둘"), "<strong>한 글</strong><br>둘")
        self.assertEqual(bold_note('<script>bad</script>'), '&lt;script&gt;bad&lt;/script&gt;')

    def test_invalid_note_retains_content_and_formatter_in_retry_form(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("record_save", args=["2026-10-07"]), {
            "today_story": "**기록**" + "a" * 2000, "expected_updated_at": "",
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "**기록**", status_code=400)
        self.assertContains(response, 'data-note-editor="true"', status_code=400)
        self.assertFalse(DailyRecord.objects.exists())

    def test_ajax_day_keeps_library_shortcuts_and_help(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("day", args=["2026-10-07"]), HTTP_HX_REQUEST="true")
        self.assertContains(response, 'aria-label="모아 둔 기록"')
        self.assertContains(response, 'aria-controls="photo-upload-help"')
        self.assertContains(response, 'data-note-editor="true"')
        self.assertNotContains(response, "장 더 올릴 수 있습니다.")
        self.assertNotContains(response, "미리보기")
        self.assertContains(response, "기본은 검은색 일반 글씨입니다.")
        self.assertContains(response, "저장 후 일기장에서 꾸밈이 표시됩니다.")

    @mock.patch("tasks.views.photo_storage_configured", return_value=True)
    @mock.patch("tasks.views.upload_private_photo", return_value=("https://store.private.blob.vercel-storage.com/photos/example.jpg", "photos/example.jpg"))
    def test_photo_upload_is_private_metadata_and_limited_to_ten(self, upload, configured):
        self.client.force_login(self.user)
        upload_url = reverse("photo_upload", args=["2026-10-02"])
        image_bytes = BytesIO()
        Image.new("RGB", (3, 2), "red").save(image_bytes, format="JPEG")
        photo = SimpleUploadedFile("memory.jpg", image_bytes.getvalue(), content_type="image/jpeg")
        response = self.client.post(upload_url, {"photo": photo, "category": DailyPhoto.Category.LIFE})
        self.assertEqual(response.status_code, 201)
        saved = DailyPhoto.objects.get()
        self.assertEqual(saved.position, 0)
        self.assertEqual(saved.category, DailyPhoto.Category.LIFE)
        self.assertEqual(saved.content_type, "image/jpeg")
        self.assertEqual(response.json()["category_label"], "Life")
        upload.assert_called_once()
        for position in range(1, 10):
            DailyPhoto.objects.create(
                record_date=date(2026, 10, 2),
                blob_url=f"https://store.private.blob.vercel-storage.com/photos/{position}.jpg",
                blob_pathname=f"photos/{position}.jpg",
                original_name=f"{position}.jpg",
                content_type="image/jpeg",
                size_bytes=53,
                position=position,
            )
        extra = SimpleUploadedFile("extra.jpg", b"\xff\xd8\xff" + b"x" * 50, content_type="image/jpeg")
        response = self.client.post(upload_url, {"photo": extra, "category": DailyPhoto.Category.ETC})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(DailyPhoto.objects.filter(record_date=date(2026, 10, 2)).count(), 10)

    @mock.patch("tasks.views.photo_storage_configured", return_value=True)
    @mock.patch("tasks.views.upload_private_photo")
    def test_photo_upload_rejects_unknown_category_before_storage_write(self, upload, configured):
        self.client.force_login(self.user)
        photo = SimpleUploadedFile("memory.jpg", b"\xff\xd8\xff" + b"x" * 50, content_type="image/jpeg")
        response = self.client.post(reverse("photo_upload", args=["2026-10-02"]), {"photo": photo, "category": "PRIVATE"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Life, Work, Etc", response.json()["error"])
        upload.assert_not_called()

    def test_photo_album_can_filter_life_work_and_etc(self):
        for index, category in enumerate((DailyPhoto.Category.LIFE, DailyPhoto.Category.WORK, DailyPhoto.Category.ETC)):
            DailyPhoto.objects.create(
                record_date=date(2026, 10, 2),
                category=category,
                blob_url=f"https://store.private.blob.vercel-storage.com/photos/{category.lower()}.jpg",
                blob_pathname=f"photos/{category.lower()}.jpg",
                original_name=f"{category.lower()}.jpg",
                content_type="image/jpeg",
                size_bytes=10,
                position=index,
            )
        self.client.force_login(self.user)
        response = self.client.get(reverse("photo_album"), {"category": DailyPhoto.Category.WORK})
        self.assertEqual(response.context["photo_count"], 1)
        self.assertContains(response, "work.jpg")
        self.assertNotContains(response, "life.jpg")
        self.assertNotContains(response, "etc.jpg")

    def test_photo_routes_require_login(self):
        photo = DailyPhoto.objects.create(
            record_date=date(2026, 10, 2),
            blob_url="https://store.private.blob.vercel-storage.com/photos/example.jpg",
            blob_pathname="photos/example.jpg",
            original_name="example.jpg",
            content_type="image/jpeg",
            size_bytes=10,
            position=0,
        )
        for url in (reverse("photo_album"), reverse("photo_manifest"), reverse("photo_content", args=[photo.id])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.url)

    @mock.patch("tasks.views.download_private_photo", return_value=b"\xff\xd8\xffphoto")
    def test_authenticated_photo_content_is_proxied(self, download):
        photo = DailyPhoto.objects.create(
            record_date=date(2026, 10, 2),
            blob_url="https://store.private.blob.vercel-storage.com/photos/example.jpg",
            blob_pathname="photos/example.jpg",
            original_name="example.jpg",
            content_type="image/jpeg",
            size_bytes=8,
            position=0,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("photo_content", args=[photo.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\xff\xd8\xffphoto")
        self.assertIn("no-store", response["Cache-Control"])
        download.assert_called_once_with(photo.blob_url)


class HolidaySyncCommandTests(TestCase):
    def test_kasi_calendar_parser_expands_ranges_and_excludes_non_holiday(self):
        html = """
        <table><tr><th>명칭</th><th>날짜(요일)</th><th>명칭</th><th>날짜(요일)</th></tr>
        <tr><td>설날</td><td>2월16일~ 2월18일<br>(월,화,수)</td>
            <td>광복절<span>*</span></td><td>8월 15일<br>(토)</td></tr>
        <tr><td>제헌절<span>*</span></td><td>* 7월 17일<br>(금)</td><td></td><td></td></tr>
        </table>
        """
        self.assertEqual(parse_kasi_holidays(html, 2026), [
            (date(2026, 2, 16), "설날"),
            (date(2026, 2, 17), "설날"),
            (date(2026, 2, 18), "설날"),
            (date(2026, 8, 15), "광복절"),
        ])

    def test_kasi_calendar_parser_requires_holiday_table(self):
        with self.assertRaises(HolidaySyncError):
            parse_kasi_holidays("<table><tr><td>다른 표</td></tr></table>", 2026)

    @mock.patch("tasks.management.commands.sync_holidays.fetch_official_holidays")
    def test_sync_holidays_upserts_official_dates(self, fetch):
        fetch.return_value = [(date(2026, 10, 3), "개천절"), (date(2026, 10, 5), "개천절 대체공휴일")]
        output = StringIO()
        call_command("sync_holidays", year=[2026], stdout=output)
        self.assertEqual(Holiday.objects.count(), 2)
        self.assertEqual(Holiday.objects.get(holiday_date=date(2026, 10, 5)).name, "개천절 대체공휴일")


class TaskRetryTests(TestCase):
    def setUp(self):
        from django.core import signing
        self.signing = signing
        backup = mock.patch('tasks.backups.ensure_daily_backup_before_change', return_value=True)
        backup.start()
        self.addCleanup(backup.stop)
        self.user = get_user_model().objects.create_user(username='retry-owner')
        self.category = Category.objects.create(name='노무')
        self.source = Task.objects.create(
            task_date=date(2026, 10, 8), category=self.category, title='검토 자료 작성',
            details='첨부자료 확인\n담당자에게 확인', is_important=True, status=Task.Status.NOT_DONE,
        )
        self.url = reverse('task_retry', args=[self.source.pk])
        self.client.force_login(self.user)

    def payload(self, **changes):
        response = self.client.get(self.url, {'fragment': '1'})
        data = dict(task_date='2026-10-09', category=str(self.category.pk), title=self.source.title,
                    details=self.source.details, is_important='on', retry_token=response.context['form'].retry_token)
        data.update(changes)
        return data

    def test_open_prefills_content_and_requires_new_date_without_creating_task(self):
        for query in ({}, {'fragment': '1'}):
            response = self.client.get(self.url, query)
            self.assertContains(response, '할 일 다시 등록')
            form = response.context['form']
            self.assertEqual(form.initial['title'], self.source.title)
            self.assertEqual(form.initial['details'], self.source.details)
            self.assertEqual(form.initial['category'], self.category.pk)
            self.assertTrue(form.initial['is_important'])
            self.assertTrue(form.fields['task_date'].required)
            self.assertIsNone(form['task_date'].value())
            self.assertContains(response, '원래 일정의 날짜와 상태는 그대로 남고')
            self.assertNotContains(response, '날짜를 비우면 보관함')
        self.assertEqual(Task.objects.count(), 1)

    def test_save_creates_independent_pending_task_and_preserves_original_history(self):
        from .models import TaskStatusHistory
        old_values = (self.source.task_date, self.source.status, self.source.updated_at)
        TaskStatusHistory.objects.create(task=self.source, from_status=Task.Status.PENDING, to_status=Task.Status.NOT_DONE)
        data = self.payload()
        response = self.client.post(self.url + '?fragment=1', data, HTTP_HX_REQUEST='true')
        self.assertRedirects(response, reverse('day', args=['2026-10-09']))
        new = Task.objects.exclude(pk=self.source.pk).get()
        self.assertNotEqual(new.pk, self.source.pk)
        self.assertEqual((new.title, new.category_id, new.details, new.is_important),
                         (self.source.title, self.category.pk, self.source.details, True))
        self.assertEqual((new.task_date, new.status), (date(2026, 10, 9), Task.Status.PENDING))
        self.assertEqual((new.status_history.get().from_status, new.status_history.get().to_status), (None, Task.Status.PENDING))
        self.source.refresh_from_db()
        self.assertEqual((self.source.task_date, self.source.status, self.source.updated_at), old_values)
        self.assertEqual(self.source.status_history.count(), 1)

    def test_post_retry_does_not_duplicate_new_task_or_history(self):
        data = self.payload()
        self.client.post(self.url, data)
        self.client.post(self.url, data)
        self.assertEqual(Task.objects.count(), 2)
        self.assertEqual(Task.objects.exclude(pk=self.source.pk).get().status_history.count(), 1)

    def test_date_cannot_be_missing_invalid_or_equal_to_original(self):
        for value in ('', 'not-a-date', '2026-10-08'):
            with self.subTest(date=value):
                response = self.client.post(self.url, self.payload(task_date=value))
                self.assertEqual(response.status_code, 200)
                self.assertIn('task_date', response.context['form'].errors)
                self.assertEqual(Task.objects.count(), 1)

    def test_invalid_content_is_retained_without_partial_insert(self):
        response = self.client.post(self.url, self.payload(title='x' * 201, details='유지할 메모'))
        self.assertEqual(response.context['form']['details'].value(), '유지할 메모')
        self.assertIn('title', response.context['form'].errors)
        self.assertEqual(Task.objects.count(), 1)

    def test_archived_source_category_can_be_preserved_without_unarchiving(self):
        self.category.is_archived = True
        self.category.save()
        self.client.post(self.url, self.payload())
        self.assertEqual(Task.objects.exclude(pk=self.source.pk).get().category_id, self.category.pk)
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_archived)

    def test_pending_and_done_tasks_cannot_be_retried(self):
        for status in (Task.Status.PENDING, Task.Status.DONE):
            self.source.status = status
            self.source.save()
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.post(self.url, {'task_date': '2026-10-09'}).status_code, 404)
        self.assertEqual(Task.objects.count(), 1)

    def test_status_change_or_delete_while_dialog_open_blocks_insert(self):
        data = self.payload()
        self.source.status = Task.Status.DONE
        self.source.save()
        self.assertEqual(self.client.post(self.url, data).status_code, 404)
        from django.utils import timezone
        self.source.status = Task.Status.NOT_DONE
        self.source.deleted_at = timezone.now()
        self.source.save()
        self.assertEqual(self.client.post(self.url, data).status_code, 404)
        self.assertEqual(Task.objects.count(), 1)

    def test_invalid_expired_and_wrong_source_tickets_are_rejected(self):
        for payload in ({'source': str(self.source.pk), 'user': 'another-user', 'new_task': str(self.source.pk)},
                        {'source': 'another-task', 'user': str(self.user.pk), 'new_task': str(self.source.pk)}):
            data = self.payload(retry_token=self.signing.dumps(payload, salt='task-retry'))
            response = self.client.post(self.url, data)
            self.assertTrue(response.context['form'].non_field_errors())
        response = self.client.post(self.url, self.payload(retry_token='tampered'))
        self.assertTrue(response.context['form'].non_field_errors())
        data = self.payload()
        with mock.patch('django.core.signing.time.time', return_value=99999999999):
            response = self.client.post(self.url, data)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertEqual(Task.objects.count(), 1)

    def test_login_and_csrf_are_required(self):
        from django.test import Client
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {'task_date': '2026-10-09'}).status_code, 403)
        self.assertEqual(Task.objects.count(), 1)

    def test_partial_retry_prefills_then_preserves_source_and_creates_pending_copy(self):
        from .models import TaskStatusHistory
        self.source.status = Task.Status.PARTIAL
        self.source.save()
        old_values = (self.source.task_date, self.source.status, self.source.updated_at)
        TaskStatusHistory.objects.create(task=self.source, from_status=Task.Status.PENDING, to_status=Task.Status.PARTIAL)
        self.test_open_prefills_content_and_requires_new_date_without_creating_task()
        data = self.payload()
        self.assertRedirects(self.client.post(self.url, data), reverse('day', args=['2026-10-09']))
        # Retrying the same submission must not create a second copy.
        self.client.post(self.url, data)
        new = Task.objects.exclude(pk=self.source.pk).get()
        self.assertEqual((new.title, new.category_id, new.details, new.is_important),
                         (self.source.title, self.category.pk, self.source.details, True))
        self.assertEqual((new.task_date, new.status), (date(2026, 10, 9), Task.Status.PENDING))
        self.assertEqual((new.status_history.get().from_status, new.status_history.get().to_status), (None, Task.Status.PENDING))
        self.source.refresh_from_db()
        self.assertEqual((self.source.task_date, self.source.status, self.source.updated_at), old_values)
        self.assertEqual(list(self.source.status_history.values_list('from_status', 'to_status')),
                         [(Task.Status.PENDING, Task.Status.PARTIAL)])

    def test_partial_retry_requires_another_date_and_blocks_completed_source(self):
        self.source.status = Task.Status.PARTIAL
        self.source.save()
        self.test_date_cannot_be_missing_invalid_or_equal_to_original()
        data = self.payload()
        self.source.status = Task.Status.DONE
        self.source.save()
        self.assertEqual(self.client.post(self.url, data).status_code, 404)
        self.assertEqual(Task.objects.count(), 1)

    def test_locked_recheck_blocks_status_changes_during_retry(self):
        for status in (Task.Status.NOT_DONE, Task.Status.PARTIAL):
            with self.subTest(status=status):
                self.source.status = status
                self.source.save()
                data = self.payload()

                def changed_before_lock():
                    Task.objects.filter(pk=self.source.pk).update(status=Task.Status.DONE)
                    return Task.objects.all()

                with mock.patch('tasks.views.Task.objects.select_for_update', side_effect=changed_before_lock):
                    self.assertEqual(self.client.post(self.url, data).status_code, 404)
                self.assertEqual(Task.objects.count(), 1)

    def test_dashboard_keeps_hidden_retry_link_for_live_status_changes(self):
        for status in (Task.Status.NOT_DONE, Task.Status.PARTIAL, Task.Status.PENDING, Task.Status.DONE):
            with self.subTest(status=status):
                self.source.status = status
                self.source.save()
                response = self.client.get(reverse('day', args=['2026-10-08']))
                self.assertContains(response, 'data-task-retry')
                self.assertContains(response, f'href="{self.url}"')
                if status in (Task.Status.NOT_DONE, Task.Status.PARTIAL):
                    self.assertNotContains(response, 'hidden>다시</a>')
                else:
                    self.assertContains(response, 'hidden>다시</a>')

    def test_archive_offers_retry_for_x_and_partial_tasks_only(self):
        for status in (Task.Status.NOT_DONE, Task.Status.PARTIAL, Task.Status.PENDING, Task.Status.DONE):
            with self.subTest(status=status):
                self.source.status = status
                self.source.save()
                response = self.client.get(reverse('task_list'))
                if status in (Task.Status.NOT_DONE, Task.Status.PARTIAL):
                    self.assertContains(response, f'href="{self.url}"')
                else:
                    self.assertNotContains(response, f'href="{self.url}"')


class TaskStatusOrderTests(TestCase):
    def setUp(self):
        backup = mock.patch('tasks.backups.ensure_daily_backup_before_change', return_value=True)
        backup.start()
        self.addCleanup(backup.stop)
        self.user = get_user_model().objects.create_user(username='status-order-owner')
        self.client.force_login(self.user)
        self.category = Category.objects.create(name='노무')
        self.day = date(2026, 10, 14)
        # Deliberately opposed to both alphabetical status order and manual order.
        self.done = self.task('완료', Task.Status.DONE, 0)
        self.partial = self.task('일부 완료', Task.Status.PARTIAL, 1)
        self.failed = self.task('하지 못함', Task.Status.NOT_DONE, 2)
        self.pending = self.task('미정 첫째', Task.Status.PENDING, 3)
        self.pending_second = self.task('미정 둘째', Task.Status.PENDING, 4)

    def task(self, title, status, order, **changes):
        values = dict(title=title, status=status, sort_order=order,
                      task_date=self.day, category=self.category)
        values.update(changes)
        return Task.objects.create(**values)

    def displayed(self):
        response = self.client.get(reverse('day', args=[self.day]))
        return response.context['grouped_tasks'][0][1]

    def ids(self, tasks):
        return [str(task.pk) for task in tasks]

    def test_daily_order_and_same_status_manual_order_survive_reload_without_writes(self):
        expected = [self.pending, self.pending_second, self.failed, self.partial, self.done]
        before = list(Task.objects.values_list('pk', 'status', 'sort_order', 'updated_at'))
        self.assertEqual(self.ids(self.displayed()), self.ids(expected))
        self.assertEqual(self.ids(self.displayed()), self.ids(expected))
        self.assertEqual(list(Task.objects.values_list('pk', 'status', 'sort_order', 'updated_at')), before)
        self.assertContains(self.client.get(reverse('day', args=[self.day])), 'data-saved-value="PENDING"')

    def test_each_status_save_returns_the_same_order_as_a_fresh_page(self):
        for status in (Task.Status.DONE, Task.Status.NOT_DONE, Task.Status.PARTIAL, Task.Status.PENDING):
            with self.subTest(status=status):
                response = self.client.post(reverse('task_status', args=[self.pending.pk]),
                                            {'status': status}, HTTP_HX_REQUEST='true')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['status'], status)
                self.assertEqual(response.json()['task_ids'], self.ids(self.displayed()))
                self.pending.refresh_from_db()
                self.assertEqual(self.pending.status, status)
        self.assertEqual(self.pending.status_history.count(), 4)
        self.assertEqual((self.pending.title, self.pending.sort_order), ('미정 첫째', 3))

    def test_non_javascript_status_save_redirects_to_sorted_page(self):
        response = self.client.post(reverse('task_status', args=[self.pending.pk]), {'status': Task.Status.DONE})
        self.assertRedirects(response, reverse('day', args=[self.day]))
        displayed = self.displayed()
        self.assertEqual(displayed[0], self.pending_second)
        self.assertEqual(displayed[-1], self.pending)

    def test_arrow_moves_only_within_the_same_status(self):
        response = self.client.post(reverse('task_move', args=[self.pending.pk]), {'direction': 'down'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.ids(self.displayed()), self.ids([
            self.pending_second, self.pending, self.failed, self.partial, self.done,
        ]))
        unchanged = (self.failed.sort_order, self.partial.sort_order, self.done.sort_order)
        self.client.post(reverse('task_move', args=[self.failed.pk]), {'direction': 'down'})
        for task in (self.failed, self.partial, self.done):
            task.refresh_from_db()
        self.assertEqual((self.failed.sort_order, self.partial.sort_order, self.done.sort_order), unchanged)

    def test_drag_canonicalizes_status_groups_and_preserves_submitted_peer_order(self):
        other_category = Category.objects.create(name='채용', sort_order=1)
        unrelated = self.task('다른 카테고리', Task.Status.PENDING, 99, category=other_category)
        submitted = [self.done, self.pending_second, self.partial, self.pending, self.failed]
        expected = [self.pending_second, self.pending, self.failed, self.partial, self.done]
        response = self.client.post(reverse('task_reorder'), {
            'category_id': self.category.pk, 'task_date': self.day.isoformat(),
            'task_ids': self.ids(submitted),
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(response.json(), {'ok': True, 'task_ids': self.ids(expected)})
        self.assertEqual(self.ids(self.displayed()), self.ids(expected))
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.sort_order, 99)

    def test_default_archive_order_is_status_first_and_explicit_date_sort_still_works(self):
        self.pending.task_date = date(2026, 10, 1)
        self.pending.save()
        response = self.client.get(reverse('task_list'))
        self.assertEqual(response.context['form']['ordering'].value(), 'status')
        self.assertEqual(self.ids(response.context['page_obj']), self.ids([
            self.pending_second, self.pending, self.failed, self.partial, self.done,
        ]))
        self.assertContains(response, '상태순 (미정 → X → △ → O)')
        response = self.client.get(reverse('task_list'), {'ordering': 'date_asc'})
        self.assertEqual(response.context['page_obj'][0], self.pending)

    def test_archive_sorts_before_pagination_and_respects_filters(self):
        Task.objects.bulk_create([Task(title=f'완료 {index}', status=Task.Status.DONE,
                                      task_date=date(2026, 10, 20), category=self.category)
                                 for index in range(51)])
        first_page = self.client.get(reverse('task_list')).context['page_obj']
        self.assertEqual(first_page[0], self.pending)
        self.assertEqual(first_page[1], self.pending_second)
        second_page = self.client.get(reverse('task_list'), {'page': '2'}).context['page_obj']
        self.assertTrue(all(task.status == Task.Status.DONE for task in second_page))
        filtered = self.client.get(reverse('task_list'), {'statuses': [Task.Status.PARTIAL]}).context['page_obj']
        self.assertEqual(self.ids(filtered), self.ids([self.partial]))

    def test_undated_tasks_use_the_same_status_order(self):
        Task.objects.update(task_date=None)
        tasks = self.client.get(reverse('inbox')).context['tasks']
        self.assertEqual(self.ids(tasks), self.ids([
            self.pending, self.pending_second, self.failed, self.partial, self.done,
        ]))


class PhotoDeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="photo-owner", password="Strong-test-pass-462!")
        self.client.force_login(self.user)
        self.photo = self.make_photo(0)
        self.other = self.make_photo(1)
        self.url = reverse("photo_delete", args=[self.photo.id])
        patcher = mock.patch("tasks.views.delete_private_photo")
        self.storage_delete = patcher.start()
        self.addCleanup(patcher.stop)

    def make_photo(self, position):
        return DailyPhoto.objects.create(
            record_date=date(2026, 10, 10), position=position,
            blob_url=f"https://store.private.blob.vercel-storage.com/photos/{position}.jpg",
            blob_pathname=f"photos/{position}.jpg", original_name=f"test-{position}.jpg",
            content_type="image/jpeg", size_bytes=10, category=DailyPhoto.Category.LIFE,
        )

    def test_day_and_album_offer_delete_for_existing_photos(self):
        for url in (reverse("day", args=[self.photo.record_date]), reverse("photo_album")):
            response = self.client.get(url)
            self.assertContains(response, f'action="{self.url}"')
            self.assertContains(response, 'data-photo-delete-form')
            self.assertContains(response, 'aria-label="test-0.jpg 사진 삭제"')
        fragment = self.client.get(reverse("day", args=[self.photo.record_date]), HTTP_HX_REQUEST="true")
        self.assertContains(fragment, f'action="{self.url}"')

    def test_unconfirmed_post_shows_confirmation_and_keeps_photo(self):
        response = self.client.post(self.url)
        self.assertContains(response, "삭제한 사진은 복구할 수 없습니다.")
        self.assertContains(response, 'name="confirm_delete" value="yes"')
        self.assertTrue(DailyPhoto.objects.filter(pk=self.photo.pk).exists())
        self.storage_delete.assert_not_called()

    def test_confirmed_delete_removes_only_selected_private_file_and_frees_a_slot(self):
        for position in range(2, 10):
            self.make_photo(position)
        response = self.client.post(self.url, {"confirm_delete": "yes"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json(), {"ok": True, "count": 9})
        self.storage_delete.assert_called_once_with(self.photo.blob_url)
        self.assertFalse(DailyPhoto.objects.filter(pk=self.photo.pk).exists())
        self.assertTrue(DailyPhoto.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(self.client.get(reverse("photo_content", args=[self.photo.pk])).status_code, 404)
        manifest = self.client.get(reverse("photo_manifest")).json()
        self.assertNotIn(reverse("photo_content", args=[self.photo.pk]), [file["url"] for file in manifest["files"]])
        with mock.patch("tasks.views.photo_storage_configured", return_value=True), mock.patch(
            "tasks.views.upload_private_photo", return_value=(self.photo.blob_url, self.photo.blob_pathname)
        ):
            image_bytes = BytesIO()
            Image.new("RGB", (2, 2), "blue").save(image_bytes, format="JPEG")
            response = self.client.post(reverse("photo_upload", args=[self.photo.record_date]), {
                "category": "LIFE", "photo": SimpleUploadedFile("replacement.jpg", image_bytes.getvalue(), content_type="image/jpeg"),
            })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["delete_url"].endswith("/delete/"))
        self.assertEqual(DailyPhoto.objects.get(original_name="replacement.jpg").position, 0)

    def test_storage_failure_keeps_database_photo_and_allows_retry(self):
        from .photo_storage import PhotoStorageError
        self.storage_delete.side_effect = PhotoStorageError("사진을 삭제하지 못했습니다.")
        response = self.client.post(self.url, {"confirm_delete": "yes"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 503)
        self.assertIn("삭제하지 못했습니다", response.json()["error"])
        self.assertEqual(DailyPhoto.objects.count(), 2)
        response = self.client.post(self.url, {"confirm_delete": "yes"})
        self.assertContains(response, "사진을 삭제하지 못했습니다.", status_code=503)
        self.assertEqual(DailyPhoto.objects.count(), 2)
        self.storage_delete.side_effect = None
        self.assertEqual(self.client.post(self.url, {"confirm_delete": "yes"}).status_code, 302)

    def test_login_owner_csrf_and_post_are_required(self):
        from django.test import Client
        self.assertEqual(self.client.get(self.url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {"confirm_delete": "yes"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {"confirm_delete": "yes"}).status_code, 302)
        intruder = get_user_model().objects.create_user(username="other-photo-owner", password="Strong-test-pass-462!")
        self.client.force_login(intruder)
        self.assertEqual(self.client.post(self.url, {"confirm_delete": "yes"}).status_code, 403)
        self.storage_delete.assert_not_called()
        self.assertEqual(DailyPhoto.objects.count(), 2)

    def test_normal_form_returns_to_same_filtered_album_without_external_redirect(self):
        response = self.client.post(self.url, {"confirm_delete": "yes", "return_to": "album", "category": "WORK", "page": "2", "next": "https://example.com"})
        self.assertEqual(response.url, reverse("photo_album") + "?category=WORK&page=2")

    def test_storage_helper_uses_sdk_delete_and_wraps_errors(self):
        from .photo_storage import delete_private_photo, PhotoStorageError
        with mock.patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "disposable-test-token"}), mock.patch("vercel.blob.delete") as delete:
            delete_private_photo(self.photo.blob_url)
            delete.assert_called_once_with(self.photo.blob_url, token="disposable-test-token")
            delete.side_effect = RuntimeError("service failure")
            with self.assertRaises(PhotoStorageError):
                delete_private_photo(self.photo.blob_url)
