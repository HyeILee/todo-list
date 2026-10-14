import secrets as _review_secrets
from urllib.parse import quote as _review_quote
_REVIEW_PASSWORD_1 = _review_secrets.token_urlsafe(32) + "@?"

"""Exercise the category selection URLs emitted by the actual templates."""
import re
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Category, DailyRecord, Task

class CategoryRouteTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.password = _REVIEW_PASSWORD_1
        cls.user = get_user_model().objects.create_user(username='category-audit', password=cls.password)
        cls.categories = [Category.objects.create(name=f'Test category {i}', is_archived=i == 2) for i in range(4)]
        cls.tasks = [Task.objects.create(category=category, task_date=timezone.localdate(), title=f'Test task {i}', status=status, is_important=i == 1) for i, (category, status) in enumerate(zip(cls.categories, [Task.Status.PENDING, Task.Status.DONE, Task.Status.PARTIAL]))]
        Task.objects.create(category=cls.categories[1], task_date=None, title='Test inbox task')
        DailyRecord.objects.create(record_date=timezone.localdate(), today_story='Test record')

    def setUp(self):
        response = self.client.post(reverse('login'), {'username': self.user.username, 'password': self.password}, follow=True)
        self.assertRedirects(response, '/')

    def test_all_categories_render_together_on_dashboard(self):
        for headers in ({}, {'HTTP_HX_REQUEST': 'true'}):
            with self.subTest(headers=headers):
                response = self.client.get('/', **headers)
                self.assertEqual(response.status_code, 200)
                for task in self.tasks:
                    self.assertContains(response, task.title)
                    self.assertContains(response, f'data-category-id="{task.category_id}"')
                self.assertEqual(len(response.context['grouped_tasks']), 3)

    def test_select_each_category_using_search_form(self):
        page = self.client.get(reverse('task_list'))
        self.assertContains(page, '<form method="get" class="filter-panel">', html=False)
        offered = re.findall('name="categories" value="([^"]+)"', page.content.decode())
        self.assertCountEqual(offered, [str(category.pk) for category in self.categories])
        for category_id in offered:
            for mode in ('dated', 'all', 'inbox'):
                with self.subTest(category_id=category_id, mode=mode):
                    response = self.client.get(reverse('task_list'), {'q': '', 'date_from': '', 'date_to': '', 'date_mode': mode, 'ordering': 'date_desc', 'categories': category_id})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context['form'].is_valid())
                    expected = Task.objects.filter(category_id=category_id)
                    if mode != 'all':
                        expected = expected.filter(task_date__isnull=mode == 'inbox')
                    self.assertCountEqual(response.context['page_obj'].object_list, expected)

    def test_task_status_and_important_filters_share_an_inline_row(self):
        response = self.client.get(reverse('task_list'))
        self.assertContains(response, 'class="status-filter-row"')
        self.assertContains(response, 'id="id_statuses"')
        self.assertContains(response, ' 중요</label>')
        self.assertNotContains(response, '중요 업무만')

    def test_each_category_edit_link(self):
        response = self.client.get(reverse('categories'))
        links = re.findall('href="(\\?edit=[^"]+)"', response.content.decode())
        self.assertEqual(len(links), 4)
        for link in links:
            with self.subTest(link=link):
                response = self.client.get(reverse('categories') + link)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(str(response.context['form'].instance.pk), link.split('=')[1])

    def test_each_task_edit_link_full_page_and_dialog(self):
        response = self.client.get('/')
        links = re.findall('href="(/tasks/[^/]+/edit/)"', response.content.decode())
        self.assertEqual(len(links), 3)
        for link in links:
            for headers in ({}, {'HTTP_HX_REQUEST': 'true'}):
                with self.subTest(link=link, headers=headers):
                    response = self.client.get(link, **headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'aria-describedby="id_title_errors"')
                    self.assertContains(response, 'id="id_title_errors"')

    def test_navigation_pages_and_empty_day(self):
        for name in ('home', 'task_list', 'record_list', 'inbox', 'settings_home', 'categories', 'task_create'):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        response = self.client.get(reverse('day', args=['2000-01-01']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['grouped_tasks'], [])
