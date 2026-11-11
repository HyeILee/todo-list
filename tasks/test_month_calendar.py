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

    def test_month_summary_counts_all_tasks_in_the_month_including_hidden_rows(self):
        expected = [
            (Task.Status.PENDING, 5, date(2026, 11, 1)),
            (Task.Status.NOT_DONE, 3, date(2026, 11, 30)),
            (Task.Status.PARTIAL, 2, date(2026, 11, 16)),
            (Task.Status.DONE, 4, date(2026, 11, 11)),
        ]
        for status, count, day in expected:
            for index in range(count):
                Task.objects.create(category=self.category, task_date=day,
                    title=f"월간 샘플 {status} {index}", status=status)
        for day in (date(2026, 10, 31), date(2026, 12, 1), None):
            Task.objects.create(category=self.category, task_date=day, title="집계 제외 샘플")
        Task.objects.create(category=self.category, task_date=date(2026, 11, 15),
            title="휴지통 샘플", status=Task.Status.DONE, deleted_at=timezone.now())

        response = self.client.get(reverse("month_calendar"), {"date": "2026-11-11"})
        self.assertEqual(response.context["month_task_count"], 14)
        self.assertEqual(response.context["month_status_summary"], [
            {"value": status, "label": status.label, "count": count}
            for status, count, _ in expected
        ])
        cell = next(day for day in response.context["calendar_days"] if day["date"] == date(2026, 11, 1))
        self.assertEqual(len(cell["tasks"]), 3)
        self.assertEqual(cell["count"], 5)
        self.assertContains(response, "2026년 11월 · 전체 14개")
        self.assertContains(response, 'aria-label="2026년 11월 상태별 할 일 수"')
        for status, count, _ in expected:
            self.assertContains(response, f'aria-label="{status.label} {count}개"')

    def test_month_picker_summary_includes_leap_day_and_excludes_neighboring_months(self):
        for day, status in [
            (date(2028, 1, 31), Task.Status.PENDING),
            (date(2028, 2, 1), Task.Status.PENDING),
            (date(2028, 2, 29), Task.Status.DONE),
            (date(2028, 3, 1), Task.Status.NOT_DONE),
        ]:
            Task.objects.create(category=self.category, task_date=day, title="기간 샘플", status=status)
        response = self.client.get(reverse("month_calendar"), {"date": "2026-11-11", "month": "2028-02"})
        self.assertEqual(response.context["month_first"], date(2028, 2, 1))
        self.assertEqual(response.context["month_task_count"], 2)
        self.assertEqual([item["count"] for item in response.context["month_status_summary"]], [1, 0, 0, 1])
        self.assertContains(response, "2028년 2월 · 전체 2개")
        self.assertContains(response, 'aria-label="X 0개"')
        self.assertContains(response, 'aria-label="△ 0개"')

    def test_empty_month_still_displays_each_status_with_zero(self):
        response = self.client.get(reverse("month_calendar"), {"month": "2027-01"})
        self.assertEqual(response.context["month_task_count"], 0)
        self.assertEqual([item["count"] for item in response.context["month_status_summary"]], [0, 0, 0, 0])
        self.assertContains(response, "2027년 1월 · 전체 0개")
        for label in ("미정", "X", "△", "O"):
            self.assertContains(response, f'aria-label="{label} 0개"')
