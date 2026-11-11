"""Real OIDC signatures and WebAuthn ceremonies with an isolated fake provider."""
import hashlib
import json
import uuid
import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from joserfc import jwt
from joserfc.jwk import RSAKey
from requests import Response

from config import google_login, passkeys
from .models import GoogleAccount, GoogleLoginAttempt, LoginSecurity, RecoveryCode, TrustedBrowser
from .test_passkeys import Authenticator

CLIENT_ID = "disposable-client.apps.googleusercontent.com"


@override_settings(PASSKEY_ORIGIN="http://testserver", APP_SINGLE_USER=True, APP_OWNER_USERNAME="",
                   IS_VERCEL=False, GOOGLE_LOGIN_ENABLED=True, GOOGLE_CLIENT_ID=CLIENT_ID,
                   GOOGLE_CLIENT_SECRET="disposable-provider-client-secret",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class GoogleLoginTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = RSAKey.generate_key(2048, parameters={"kid": "fixture-key"})

    def setUp(self):
        self.password = "Disposable-owner-password-932!"
        self.user = get_user_model().objects.create_user(username="private-owner", password=self.password)
        self.device = Authenticator()
        self.client.defaults["HTTP_USER_AGENT"] = "Disposable browser"
        self.client.force_login(self.user)
        start = self.post_json("passkey_register_options", {"password": self.password}).json()
        proof = self.post_json("passkey_register_verify", {"ceremony": start["ceremony"],
            "credential": self.device.register(start["options"]["challenge"])}).json()
        self.codes = proof["codes"]
        self.assertEqual(self.post_json("login_security_action", {"action": "enable", "codes_saved": True}).status_code, 200)
        self.token_requests = []

    def post_json(self, name, value, client=None):
        return (client or self.client).post(reverse(name), json.dumps(value), content_type="application/json")

    def start(self, *, purpose="login", client=None, next_path="/"):
        client = client or self.client
        response = client.post(reverse("google_link" if purpose == "link" else "google_login"),
            {"password": self.password, "next": next_path})
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(urlsplit(response.url).hostname, "accounts.google.com")
        return parse_qs(urlsplit(response.url).query)

    def callback(self, query, *, client=None, changes=None, bad_signature=False):
        claims = {"iss": "https://accounts.google.com", "sub": "google-owner-subject",
                  "aud": CLIENT_ID, "nonce": query["nonce"][0],
                  "iat": int(timezone.now().timestamp()), "exp": int(timezone.now().timestamp()) + 300,
                  "email": "disposable-owner@example.test", "email_verified": True}
        claims.update(changes or {})
        signing_key = RSAKey.generate_key(2048, parameters={"kid": "fixture-key"}) if bad_signature else self.key
        encoded = jwt.encode({"alg": "RS256", "kid": "fixture-key"}, claims, signing_key)

        def provider_send(request, **kwargs):
            response = Response()
            response.status_code = 200
            response.url = request.url
            response.headers["Content-Type"] = "application/json"
            if request.url == "https://oauth2.googleapis.com/token":
                self.token_requests.append(parse_qs(request.body))
                data = {"access_token": "disposable-access-token", "refresh_token": "disposable-refresh-token",
                        "id_token": encoded, "token_type": "Bearer", "expires_in": 3600}
            elif request.url == "https://www.googleapis.com/oauth2/v3/certs":
                data = {"keys": [self.key.as_dict(private=False)]}
            else:
                raise AssertionError("Unexpected provider endpoint: " + request.url)
            response._content = json.dumps(data).encode()
            return response

        with patch("requests.sessions.Session.send", side_effect=provider_send):
            return (client or self.client).get(reverse("google_callback"),
                {"state": query["state"][0], "code": "disposable-authorization-code"})

    def link(self):
        query = self.start(purpose="link")
        self.assertRedirects(self.callback(query), reverse("login_security"), fetch_redirect_response=False)
        return GoogleAccount.objects.get(user=self.user)

    def passkey_login(self, *, client=None, count=1, remember=False):
        start = self.post_json("passkey_auth_options", {}, client).json()
        return self.post_json("passkey_auth_verify", {"ceremony": start["ceremony"],
            "credential": self.device.assertion(start["options"]["challenge"], count=count), "remember": remember}, client)

    def test_link_preserves_the_existing_user_and_uses_pkce_minimal_scopes(self):
        query = self.start(purpose="link")
        self.assertEqual(set(query["scope"][0].split()), {"openid", "email"})
        self.assertEqual(query["redirect_uri"], ["http://testserver/accounts/google/callback/"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["access_type"], ["online"])
        self.assertNotIn("client_secret", query)
        self.assertEqual(self.callback(query).status_code, 302)
        self.assertEqual(GoogleAccount.objects.get().user_id, self.user.pk)
        self.assertEqual(get_user_model().objects.count(), 1)
        posted = self.token_requests[0]
        self.assertEqual(posted["redirect_uri"], query["redirect_uri"])
        self.assertEqual(posted["client_id"], [CLIENT_ID])
        challenge = passkeys.bytes_to_base64url(hashlib.sha256(posted["code_verifier"][0].encode()).digest())
        self.assertEqual(query["code_challenge"], [challenge])
        stored = json.dumps(dict(self.client.session))
        self.assertNotIn("disposable-access-token", stored)
        self.assertNotIn("disposable-refresh-token", stored)
        self.assertNotIn("id_token", stored)
        self.assertEqual(GoogleLoginAttempt.objects.get().state_hash, passkeys.digest(query["state"][0], "google-state"))

    def test_unknown_browser_cannot_reach_records_before_real_passkey_confirmation(self):
        self.link()
        self.client.logout()
        query = self.start(next_path="/day/2026-11-11/")
        self.assertRedirects(self.callback(query), reverse("passkey_challenge"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.get(reverse("home")), reverse("login") + "?next=/", fetch_redirect_response=False)
        self.assertEqual(self.passkey_login().status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertEqual(self.client.session["dugyeon_google_account"], GoogleAccount.objects.get().pk)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_remembered_browser_requires_cookie_same_ip_and_agent(self):
        self.link()
        self.client.logout()
        self.callback(self.start())
        proof = self.passkey_login(remember=True)
        cookie = proof.cookies[passkeys.COOKIE].value
        for agent, ip, trusted in [("Disposable browser", "127.0.0.1", True),
                                    ("Disposable browser", "127.0.0.2", False),
                                    ("Other browser", "127.0.0.1", False)]:
            with self.subTest(agent=agent, ip=ip):
                client = Client(HTTP_USER_AGENT=agent, REMOTE_ADDR=ip)
                client.cookies[passkeys.COOKIE] = cookie
                self.callback(self.start(client=client), client=client)
                self.assertEqual("_auth_user_id" in client.session, trusted)
        client = Client(HTTP_USER_AGENT="Disposable browser")
        self.callback(self.start(client=client), client=client)
        self.assertNotIn("_auth_user_id", client.session)

    def test_other_subject_is_denied_even_if_email_matches(self):
        self.link()
        self.client.logout()
        response = self.callback(self.start(), changes={"sub": "somebody-else"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_email_change_does_not_break_linked_subject(self):
        self.link()
        self.client.logout()
        response = self.callback(self.start(), changes={"email": "changed-address@example.test"})
        self.assertEqual(response.url, reverse("passkey_challenge"))

    def test_oidc_rejects_bad_signature_nonce_audience_issuer_expiry_and_unverified_email(self):
        self.link()
        self.client.logout()
        cases = [{"nonce": "wrong"}, {"aud": "wrong-client"}, {"iss": "https://attacker.test"},
                 {"exp": int(timezone.now().timestamp()) - 1000}, {"email_verified": False},
                 {"email_verified": "true"}, {"email": "invalid"}, {"sub": ""}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.callback(self.start(), changes=changes).status_code, 400)
                self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.callback(self.start(), bad_signature=True).status_code, 400)

    def test_callback_state_is_bound_to_session_and_cannot_be_replayed(self):
        query = self.start(purpose="link")
        other = Client()
        self.assertEqual(self.callback(query, client=other).status_code, 400)
        self.assertFalse(GoogleAccount.objects.exists())
        self.assertEqual(self.callback(query).status_code, 302)
        calls = len(self.token_requests)
        self.assertEqual(self.callback(query).status_code, 400)
        self.assertEqual(len(self.token_requests), calls)

    def test_expired_intent_and_missing_library_state_are_denied(self):
        query = self.start(purpose="link")
        GoogleLoginAttempt.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.callback(query).status_code, 400)
        query = self.start(purpose="link")
        session = self.client.session
        for key in list(session.keys()):
            if key.startswith("_state_google_"):
                del session[key]
        session.save()
        self.assertEqual(self.callback(query).status_code, 400)
        self.assertFalse(GoogleAccount.objects.exists())

    def test_link_requires_password_recent_passkey_and_enabled_security(self):
        self.assertEqual(self.client.post(reverse("google_link"), {"password": "wrong"}).status_code, 400)
        session = self.client.session
        session["dugyeon_mfa_at"] = timezone.now().timestamp() - 301
        session.save()
        self.assertEqual(self.client.post(reverse("google_link"), {"password": self.password}).status_code, 400)
        LoginSecurity.objects.filter(user=self.user).update(enabled=False)
        self.assertEqual(self.client.post(reverse("google_link"), {"password": self.password}).status_code, 400)
        self.assertFalse(GoogleLoginAttempt.objects.exists())

    def test_link_refuses_changed_security_generation_and_replacement(self):
        query = self.start(purpose="link")
        LoginSecurity.objects.filter(user=self.user).update(generation=uuid.uuid4())
        self.assertRedirects(self.callback(query), reverse("login"), fetch_redirect_response=False)
        self.assertFalse(GoogleAccount.objects.exists())

    def test_existing_link_cannot_be_overwritten(self):
        account = self.link()
        response = self.client.post(reverse("google_link"), {"password": self.password})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(GoogleAccount.objects.get().subject, account.subject)

    def test_password_change_during_link_cancels_the_original_proof(self):
        query = self.start(purpose="link")
        self.user.set_password("Changed-disposable-password-582!")
        self.user.save()
        self.callback(query)
        self.assertFalse(GoogleAccount.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_during_link_cancels_the_original_session(self):
        query = self.start(purpose="link")
        self.client.logout()
        self.assertEqual(self.callback(query).status_code, 400)
        self.assertFalse(GoogleAccount.objects.exists())

    def test_unlink_requires_both_fresh_passkey_and_correct_password(self):
        self.link()
        self.assertEqual(self.client.post(reverse("google_unlink"), {"password": "wrong"}).status_code, 400)
        session = self.client.session
        session["dugyeon_mfa_at"] = timezone.now().timestamp() - 301
        session.save()
        self.assertEqual(self.client.post(reverse("google_unlink"), {"password": self.password}).status_code, 400)
        self.assertTrue(GoogleAccount.objects.filter(user=self.user).exists())

    def test_no_automatic_google_signup_or_non_owner_login(self):
        self.client.logout()
        self.assertEqual(self.callback(self.start()).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 1)
        other = get_user_model().objects.create_user(username="other", password=self.password)
        GoogleAccount.objects.create(user=other, subject="google-owner-subject", email="other@example.test")
        LoginSecurity.objects.create(user=other, enabled=True)
        self.assertEqual(self.callback(self.start()).status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_google_cannot_disable_additional_verification(self):
        self.link()
        response = self.post_json("login_security_action", {"action": "disable", "password": self.password})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(LoginSecurity.objects.get(user=self.user).enabled)

    def test_unlink_revokes_other_google_sessions_and_keeps_recovery_codes_usable(self):
        self.link()
        other = Client(HTTP_USER_AGENT="Disposable browser")
        self.callback(self.start(client=other), client=other)
        self.assertEqual(self.passkey_login(client=other, remember=True).status_code, 200)
        original_generation = LoginSecurity.objects.get(user=self.user).generation
        self.assertEqual(self.client.post(reverse("google_unlink"), {"password": self.password}).status_code, 302)
        self.assertFalse(GoogleAccount.objects.exists())
        self.assertEqual(LoginSecurity.objects.get(user=self.user).generation, original_generation)
        self.assertFalse(TrustedBrowser.objects.filter(revoked_at__isnull=True).exists())
        self.assertEqual(other.get(reverse("home")).status_code, 302)
        self.assertNotIn("_auth_user_id", other.session)
        other.post(reverse("login"), {"username": self.user.username, "password": self.password})
        self.assertEqual(self.post_json("passkey_recovery", {"code": self.codes[0]}, other).status_code, 200)
        self.assertTrue(RecoveryCode.objects.filter(used_at__isnull=True).exists())

    def test_unlink_also_revokes_google_logins_waiting_for_passkey(self):
        self.link()
        other = Client(HTTP_USER_AGENT="Disposable browser")
        self.callback(self.start(client=other), client=other)
        self.client.post(reverse("google_unlink"), {"password": self.password})
        self.assertEqual(self.post_json("passkey_auth_options", {}, other).status_code, 400)

    def test_all_mutations_require_post_and_csrf(self):
        for name in ["google_login", "google_link", "google_unlink"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
            client = Client(enforce_csrf_checks=True)
            client.cookies[settings.SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
            self.assertEqual(client.post(reverse(name), {"password": self.password}).status_code, 403)

    def test_disabled_configuration_hides_google_and_keeps_password_login(self):
        self.link()
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), "Google 계정으로 로그인")
        with override_settings(GOOGLE_LOGIN_ENABLED=False):
            self.assertNotContains(self.client.get(reverse("login")), "Google 계정으로 로그인")
            self.assertEqual(self.client.post(reverse("google_login")).status_code, 503)
            response = self.client.post(reverse("login"), {"username": self.user.username, "password": self.password})
            self.assertEqual(response.url, reverse("passkey_challenge"))

    def test_external_next_redirects_and_old_domain_are_not_used(self):
        for candidate in ["https://attacker.test/", "//attacker.test/", "/\\attacker.test/", "/\r\nLocation: bad"]:
            self.assertEqual(google_login.safe_next(candidate), "/")
        self.assertEqual(google_login.safe_next("/tasks/all/?q=fixture"), "/tasks/all/?q=fixture")
        with override_settings(ALLOWED_HOSTS=["testserver", "old.test"]):
            self.assertEqual(self.client.post(reverse("google_link"), {"password": self.password}, HTTP_HOST="old.test").status_code, 400)

    def test_provider_cancel_does_not_leak_error_details(self):
        query = self.start(purpose="link")
        response = self.client.get(reverse("google_callback"), {"state": query["state"][0],
            "error": "access_denied", "error_description": "provider-secret-that-must-not-be-shown"})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "provider-secret-that-must-not-be-shown", status_code=400)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertFalse(GoogleAccount.objects.exists())

    def test_start_requests_are_bounded_by_network(self):
        self.client.logout()
        for _ in range(20):
            self.start()
        self.assertEqual(self.client.post(reverse("google_login")).status_code, 429)


class GoogleBackupTests(TransactionTestCase):
    def test_portable_data_backup_never_exports_or_replaces_google_identities(self):
        from .portable_backups import create_data_backup, restore_validated_data, validate_data_backup
        owner = get_user_model().objects.create_user(username="backup-fixture")
        linked = GoogleAccount.objects.create(user=owner, subject="private-google-subject", email="private@example.test")
        archive, _ = create_data_backup()
        with zipfile.ZipFile(BytesIO(archive)) as files:
            data = files.read("data.json").decode()
            self.assertNotIn("googleaccount", data)
            self.assertNotIn("googleloginattempt", data)
            self.assertNotIn("private@example.test", data)
        objects, _ = validate_data_backup(archive)
        restore_validated_data(objects)
        self.assertTrue(GoogleAccount.objects.filter(pk=linked.pk, user=owner).exists())

    def test_previous_portable_schedule_backup_remains_compatible(self):
        from .portable_backups import create_data_backup, validate_data_backup
        archive, _ = create_data_backup()
        with zipfile.ZipFile(BytesIO(archive)) as original:
            files = {name: original.read(name) for name in original.namelist()}
        manifest = json.loads(files["manifest.json"])
        manifest["db_schema_version"] = "0009_conditional_passkeys"
        files["manifest.json"] = json.dumps(manifest).encode()
        result = BytesIO()
        with zipfile.ZipFile(result, "w") as rewritten:
            for name, content in files.items():
                rewritten.writestr(name, content)
        validate_data_backup(result.getvalue())
