from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Category, Holiday, Task


class MonthCalendarTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="calendar-test", password="example-only")
        self.client.force_login(self.user)
        self.category = Category.objects.create(name="샘플")

    def test_week_button_opens_the_selected_month_in_a_new_window(self):
        response = self.client.get(reverse("day", args=["2026-11-02"]))
        self.assertContains(response, 'href="/calendar/?date=2026-11-02" target="_blank" rel="noopener"')
        self.assertContains(response, ">한 달</a>")

    def test_calendar_shows_full_weeks_and_fades_neighboring_months(self):
        Holiday.objects.create(holiday_date=date(2026, 11, 1), name="샘플 휴일")
        Task.objects.create(task_date=date(2026, 10, 31), category=self.category, title="전월 샘플")
        Task.objects.create(task_date=date(2026, 11, 2), category=self.category, title="미정 샘플")
        Task.objects.create(task_date=date(2026, 11, 2), category=self.category, title="완료 샘플", status=Task.Status.DONE)
        Task.objects.create(task_date=date(2026, 11, 2), category=self.category, title="삭제 샘플", deleted_at=timezone.now())
        response = self.client.get(reverse("month_calendar"), {"date": "2026-11-02"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month_first"], date(2026, 11, 1))
        cells = {day["date"]: day for day in response.context["calendar_days"]}
        self.assertEqual(len(cells), 42)
        self.assertTrue(cells[date(2026, 10, 31)]["outside"])
        self.assertEqual([task.title for task in cells[date(2026, 11, 2)]["tasks"]], ["미정 샘플", "완료 샘플"])
        self.assertEqual(cells[date(2026, 11, 2)]["count"], 2)
        self.assertEqual(cells[date(2026, 11, 1)]["calendar_class"], "holiday")
        self.assertContains(response, 'class="month-day saturday outside')
        self.assertContains(response, 'href="/calendar/?month=2026-10"')
        self.assertContains(response, 'href="/calendar/?month=2026-12"')
        self.assertNotContains(response, "삭제 샘플")

    def test_month_picker_and_overflow_link_to_full_day(self):
        for i in range(5):
            Task.objects.create(task_date=date(2026, 12, 3), category=self.category, title=f"샘플 {i}")
        response = self.client.get(reverse("month_calendar"), {"date": "2026-11-02", "month": "2026-12"})
        self.assertEqual(response.context["month_first"], date(2026, 12, 1))
        cell = next(day for day in response.context["calendar_days"] if day["date"] == date(2026, 12, 3))
        self.assertEqual(cell["count"], 5)
        self.assertEqual(cell["more_count"], 2)
        self.assertContains(response, 'value="2026-12"')
        self.assertContains(response, 'href="/day/2026-12-03/"')
        self.assertContains(response, "+ 2개 더 보기")
        invalid = self.client.get(reverse("month_calendar"), {"date": "2026-11-02", "month": "invalid"})
        self.assertEqual(invalid.context["month_first"], date(2026, 11, 1))

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(reverse("month_calendar"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)
