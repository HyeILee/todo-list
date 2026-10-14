from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone


class SessionSecurityTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="session-owner", password="Long-fixture-pass-841")
        self.client.force_login(self.owner)

    def test_activity_requires_post_authentication_and_csrf(self):
        url = reverse("session_activity")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url).status_code, 401)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        self.assertEqual(protected.post(url).status_code, 403)
        protected.get(reverse("home"))
        response = protected.post(url, HTTP_X_CSRFTOKEN=protected.cookies[settings.CSRF_COOKIE_NAME].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertIn("no-store", response["Cache-Control"])

    def test_active_session_is_refreshed_but_expired_session_is_not_revived(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 1800)
        session_key = self.client.session.session_key
        Session.objects.filter(session_key=session_key).update(expire_date=timezone.now() + timedelta(seconds=20))
        self.assertEqual(self.client.post(reverse("session_activity")).status_code, 200)
        self.assertGreater(Session.objects.get(session_key=session_key).expire_date, timezone.now() + timedelta(minutes=29))
        Session.objects.filter(session_key=session_key).update(expire_date=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.post(reverse("session_activity")).status_code, 401)
        self.assertEqual(self.client.get(reverse("home")).status_code, 302)

    def test_unused_admin_routes_are_unavailable(self):
        for url in ("/admin/", "/admin/login/", "/admin/auth/user/"):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(Client().get(url).status_code, 404)
