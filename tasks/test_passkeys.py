"""End-to-end verification with real disposable WebAuthn signatures, no production keys."""
import hashlib
import json
import secrets
from datetime import timedelta
from io import BytesIO
import zipfile

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from webauthn.helpers import bytes_to_base64url as b64

from config.passkeys import COOKIE, PENDING, digest
from .models import LoginSecurity, Passkey, PasskeyChallenge, RecoveryCode, TrustedBrowser


class Authenticator:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        numbers = self.private.public_key().public_numbers()
        self.cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, 'big'), -3: numbers.y.to_bytes(32, 'big')})

    def client_data(self, challenge, kind, origin='http://testserver'):
        return json.dumps({'type': kind, 'challenge': challenge, 'origin': origin, 'crossOrigin': False}).encode()

    def register(self, challenge, *, origin='http://testserver', rp='testserver', uv=True):
        data = self.client_data(challenge, 'webauthn.create', origin)
        flags = 0x41 | (4 if uv else 0)
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + (0).to_bytes(4, 'big')
        auth += bytes(16) + len(self.id).to_bytes(2, 'big') + self.id + self.cose
        return {'id': b64(self.id), 'rawId': b64(self.id), 'type': 'public-key', 'response': {
            'clientDataJSON': b64(data), 'attestationObject': b64(cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth})), 'transports': ['internal']}}

    def assertion(self, challenge, *, origin='http://testserver', rp='testserver', uv=True, count=1):
        data = self.client_data(challenge, 'webauthn.get', origin)
        auth = hashlib.sha256(rp.encode()).digest() + bytes([1 | (4 if uv else 0)]) + count.to_bytes(4, 'big')
        signature = self.private.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64(self.id), 'rawId': b64(self.id), 'type': 'public-key', 'response': {
            'clientDataJSON': b64(data), 'authenticatorData': b64(auth), 'signature': b64(signature)}}


@override_settings(PASSKEY_ORIGIN='http://testserver', APP_SINGLE_USER=True, APP_OWNER_USERNAME='', IS_VERCEL=False)
class PasskeyFlowTests(TestCase):
    def setUp(self):
        self.password = 'Disposable-passkey-462!'
        self.user = get_user_model().objects.create_user(username='passkey-owner', password=self.password)
        self.device = Authenticator()
        self.client.defaults['HTTP_USER_AGENT'] = 'Fixture browser'

    def post(self, name, data, client=None):
        return (client or self.client).post(reverse(name), data=json.dumps(data), content_type='application/json')

    def enroll(self, enable=True, remember=False):
        self.client.force_login(self.user)
        options = self.post('passkey_register_options', {'password': self.password})
        self.assertEqual(options.status_code, 200, options.content)
        start = options.json()
        result = self.post('passkey_register_verify', {'ceremony': start['ceremony'],
            'credential': self.device.register(start['options']['challenge']), 'label': '<fixture>'})
        self.assertEqual(result.status_code, 200, result.content)
        self.codes = result.json()['codes']
        if enable:
            result = self.post('login_security_action', {'action': 'enable', 'codes_saved': True, 'remember': remember})
            self.assertEqual(result.status_code, 200, result.content)
        return result

    def password_login(self, client=None, **kwargs):
        client = client or self.client
        return client.post(reverse('login'), {'username': self.user.username, 'password': self.password}, **kwargs)

    def login_assertion(self, *, client=None, count=1, remember=False, **kwargs):
        client = client or self.client
        start = self.post('passkey_auth_options', {}, client).json()
        return self.post('passkey_auth_verify', {'ceremony': start['ceremony'],
            'credential': self.device.assertion(start['options']['challenge'], count=count, **kwargs), 'remember': remember}, client)

    def test_unenrolled_login_keeps_existing_flow(self):
        self.assertEqual(self.password_login().status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get(reverse('login_security')).status_code, 200)

    def test_registration_needs_password_and_does_not_enable_until_acknowledged(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post('passkey_register_options', {'password': 'bad'}).status_code, 400)
        self.enroll(enable=False)
        profile = LoginSecurity.objects.get(user=self.user)
        self.assertFalse(profile.enabled)
        self.assertEqual(len(self.codes), 10)
        self.assertFalse(RecoveryCode.objects.filter(code_hash__in=self.codes).exists())
        for value in [False, 'yes', 'false', None]:
            self.assertEqual(self.post('login_security_action', {'action': 'enable', 'codes_saved': value}).status_code, 400)
        self.assertEqual(self.post('login_security_action', {'action': 'enable', 'codes_saved': True}).status_code, 200)
        self.assertIn('no-store', self.client.get(reverse('login_security'))['Cache-Control'])
        self.assertContains(self.client.get(reverse('login_security')), '&lt;fixture&gt;')

    def test_password_only_cannot_access_records_when_enabled(self):
        self.enroll()
        self.client.logout()
        response = self.password_login()
        self.assertRedirects(response, reverse('passkey_challenge'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertIn(PENDING, self.client.session)
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)
        self.assertContains(self.client.get(reverse('passkey_challenge')), '본인 확인')
        self.assertEqual(self.login_assertion().status_code, 200)
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertEqual(Passkey.objects.get().sign_count, 1)

    def test_remember_requires_cookie_same_ip_and_browser(self):
        self.enroll()
        self.client.logout()
        self.password_login()
        response = self.login_assertion(remember=True)
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['max-age'], 30 * 86400)
        self.assertNotEqual(TrustedBrowser.objects.get().token_hash, cookie.value)
        good = Client(HTTP_USER_AGENT='Fixture browser')
        good.cookies[COOKIE] = cookie.value
        self.password_login(good)
        self.assertIn('_auth_user_id', good.session)
        for agent, ip, token in [('Changed browser', '127.0.0.1', cookie.value), ('Fixture browser', '127.0.0.2', cookie.value), ('Fixture browser', '127.0.0.1', 'fake')]:
            other = Client(HTTP_USER_AGENT=agent)
            other.cookies[COOKIE] = token
            self.password_login(other, REMOTE_ADDR=ip)
            self.assertNotIn('_auth_user_id', other.session)
            self.assertIn(PENDING, other.session)
        # Same IP alone must never replace the remembered browser secret.
        other = Client(HTTP_USER_AGENT='Fixture browser')
        self.password_login(other)
        self.assertNotIn('_auth_user_id', other.session)

    def test_expired_and_revoked_browser_require_passkey(self):
        response = self.enroll(remember=True)
        token = response.cookies[COOKIE].value
        for change in [{'expires_at': timezone.now() - timedelta(seconds=1)}, {'expires_at': timezone.now() + timedelta(days=1), 'revoked_at': timezone.now()}]:
            TrustedBrowser.objects.update(**change)
            other = Client(HTTP_USER_AGENT='Fixture browser')
            other.cookies[COOKIE] = token
            self.password_login(other)
            self.assertNotIn('_auth_user_id', other.session)

    def test_real_signatures_reject_origin_rp_challenge_uv_and_invalid_signature(self):
        self.enroll()
        self.client.logout()
        self.password_login()
        cases = [{'origin': 'https://other.test'}, {'rp': 'other.test'}, {'uv': False}, {'wrong_challenge': True}, {'bad_signature': True}]
        for case in cases:
            with self.subTest(case=case):
                start = self.post('passkey_auth_options', {}).json()
                changes = dict(case)
                wrong = changes.pop('wrong_challenge', False)
                corrupt = changes.pop('bad_signature', False)
                credential = self.device.assertion(b64(b'wrong') if wrong else start['options']['challenge'], **changes)
                if corrupt: credential['response']['signature'] = b64(b'invalid')
                response = self.post('passkey_auth_verify', {'ceremony': start['ceremony'], 'credential': credential})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('_auth_user_id', self.client.session)
                self.assertIsNotNone(PasskeyChallenge.objects.get(pk=start['ceremony']).consumed_at)
        self.assertEqual(self.post('passkey_auth_options', {}).status_code, 429)

    def test_registration_rejects_origin_rp_missing_uv_and_replay(self):
        self.client.force_login(self.user)
        for case in [{'origin': 'https://other.test'}, {'rp': 'other.test'}, {'uv': False}]:
            start = self.post('passkey_register_options', {'password': self.password}).json()
            response = self.post('passkey_register_verify', {'ceremony': start['ceremony'], 'credential': self.device.register(start['options']['challenge'], **case)})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Passkey.objects.count(), 0)
        start = self.post('passkey_register_options', {'password': self.password}).json()
        data = {'ceremony': start['ceremony'], 'credential': self.device.register(start['options']['challenge'])}
        self.assertEqual(self.post('passkey_register_verify', data).status_code, 200)
        self.assertEqual(self.post('passkey_register_verify', data).status_code, 400)

    def test_ceremony_replay_cross_session_and_counter_rejected(self):
        self.enroll()
        self.client.logout()
        self.password_login()
        start = self.post('passkey_auth_options', {}).json()
        other = Client(HTTP_USER_AGENT='Fixture browser')
        self.password_login(other)
        data = {'ceremony': start['ceremony'], 'credential': self.device.assertion(start['options']['challenge'])}
        self.assertEqual(self.post('passkey_auth_verify', data, other).status_code, 400)
        self.assertEqual(self.post('passkey_auth_verify', data).status_code, 200)
        self.assertEqual(self.post('passkey_auth_verify', data).status_code, 400)
        self.client.logout()
        self.password_login()
        self.assertEqual(self.login_assertion(count=1).status_code, 400)
        self.assertEqual(self.login_assertion(count=2).status_code, 200)

    def test_recovery_is_password_first_one_use_and_never_remembers(self):
        self.enroll()
        self.client.logout()
        self.assertEqual(self.post('passkey_recovery', {'code': self.codes[0]}).status_code, 400)
        self.password_login()
        response = self.post('passkey_recovery', {'code': self.codes[0], 'remember': True})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(COOKIE, response.cookies)
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(RecoveryCode.objects.filter(used_at__isnull=False).count(), 1)
        self.client.logout()
        self.password_login()
        self.assertEqual(self.post('passkey_recovery', {'code': self.codes[0]}).status_code, 400)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_recovery_login_can_add_replacement_passkey(self):
        self.enroll()
        self.client.logout()
        self.password_login()
        self.post('passkey_recovery', {'code': self.codes[0]})
        start = self.post('passkey_register_options', {'password': self.password}).json()
        replacement = Authenticator()
        response = self.post('passkey_register_verify', {'ceremony': start['ceremony'], 'credential': replacement.register(start['options']['challenge'])})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Passkey.objects.count(), 2)

    def test_recovery_throttling_persists_across_browser_sessions(self):
        self.enroll()
        self.client.logout()
        for _ in range(5):
            other = Client()
            self.password_login(other)
            self.assertEqual(self.post('passkey_recovery', {'code': 'bad'}, other).status_code, 400)
        self.password_login()
        self.assertEqual(self.post('passkey_recovery', {'code': self.codes[0]}).status_code, 429)

    def test_pending_and_ceremony_expire(self):
        self.enroll()
        self.client.logout()
        self.password_login()
        start = self.post('passkey_auth_options', {}).json()
        PasskeyChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post('passkey_auth_verify', {'ceremony': start['ceremony'], 'credential': self.device.assertion(start['options']['challenge'])}).status_code, 400)
        session = self.client.session
        pending = session[PENDING]; pending['at'] -= 301; session[PENDING] = pending; session.save()
        self.assertEqual(self.post('passkey_auth_options', {}).status_code, 400)
        self.assertNotIn(PENDING, self.client.session)

    def test_activation_expires_and_cannot_move_between_sessions(self):
        self.enroll(enable=False)
        other = Client(); other.force_login(self.user)
        self.assertEqual(self.post('login_security_action', {'action': 'enable', 'codes_saved': True}, other).status_code, 400)
        session = self.client.session
        proof = session['dugyeon_enroll_proof']; proof['at'] -= 601; session['dugyeon_enroll_proof'] = proof; session.save()
        self.assertEqual(self.post('login_security_action', {'action': 'enable', 'codes_saved': True}).status_code, 400)

    def test_interrupted_setup_resumes_with_existing_passkey_and_new_codes(self):
        self.enroll(enable=False)
        old_code = self.codes[0]
        self.client.logout(); self.password_login()
        self.assertContains(self.client.get(reverse('login_security')), '등록을 이어서 완료하기')
        self.assertEqual(self.post('passkey_auth_options', {}).status_code, 400)
        start = self.post('passkey_auth_options', {'password': self.password}).json()
        response = self.post('passkey_auth_verify', {'ceremony': start['ceremony'], 'credential': self.device.assertion(start['options']['challenge'])})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['codes']), 10)
        self.assertNotIn(old_code, response.json()['codes'])
        self.assertEqual(Passkey.objects.count(), 1)
        self.assertEqual(self.post('login_security_action', {'action': 'enable', 'codes_saved': True}).status_code, 200)

    def test_old_sessions_are_invalidated_when_enabled(self):
        old = Client(); old.force_login(self.user)
        self.enroll()
        self.assertRedirects(old.get(reverse('home')), reverse('login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', old.session)

    def test_management_requires_recent_verification_and_password(self):
        response = self.enroll(remember=True)
        other = Client(HTTP_USER_AGENT='Fixture browser'); other.cookies[COOKIE] = response.cookies[COOKIE].value
        self.password_login(other)
        self.assertEqual(self.post('login_security_action', {'action': 'codes', 'password': self.password}, other).status_code, 400)
        self.assertEqual(self.post('login_security_action', {'action': 'forget', 'password': 'wrong'}).status_code, 400)
        self.assertEqual(self.post('login_security_action', {'action': 'forget', 'password': self.password}).status_code, 200)
        self.assertFalse(TrustedBrowser.objects.filter(revoked_at__isnull=True).exists())

    def test_disable_revokes_keys_codes_and_browser_tokens(self):
        self.enroll(remember=True)
        response = self.post('login_security_action', {'action': 'disable', 'password': self.password})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LoginSecurity.objects.get().enabled)
        self.assertFalse(Passkey.objects.filter(revoked_at__isnull=True).exists())
        self.assertFalse(RecoveryCode.objects.filter(used_at__isnull=True).exists())
        self.assertFalse(TrustedBrowser.objects.filter(revoked_at__isnull=True).exists())

    def test_password_change_invalidates_pending_and_trusted_tokens(self):
        response = self.enroll(remember=True)
        token = response.cookies[COOKIE].value
        self.client.logout(); self.password_login()
        self.user.set_password('New-fixture-pass-792!'); self.user.save()
        self.assertEqual(self.post('passkey_auth_options', {}).status_code, 400)
        other = Client(HTTP_USER_AGENT='Fixture browser'); other.cookies[COOKIE] = token
        result = other.post(reverse('login'), {'username': self.user.username, 'password': 'New-fixture-pass-792!'})
        self.assertEqual(result.status_code, 302)
        self.assertNotIn('_auth_user_id', other.session)

    def test_recovery_codes_survive_password_change_but_require_new_password(self):
        self.enroll()
        self.client.logout()
        self.user.set_password('New-fixture-pass-792!'); self.user.save()
        self.client.post(reverse('login'), {'username': self.user.username, 'password': 'New-fixture-pass-792!'})
        self.assertEqual(self.post('passkey_recovery', {'code': self.codes[0]}).status_code, 200)

    def test_malformed_and_missing_ceremony_are_controlled_errors(self):
        self.enroll(); self.client.logout(); self.password_login()
        for ceremony in ['not-a-uuid', None, {}, []]:
            self.assertEqual(self.post('passkey_auth_verify', {'ceremony': ceremony, 'credential': {}}).status_code, 400)
        for data in [[], 'bad', {'credential': {}}]:
            self.assertEqual(self.post('passkey_auth_verify', data).status_code, 400)
        start = self.post('passkey_auth_options', {}).json()
        self.assertEqual(self.post('passkey_auth_verify', {'ceremony': start['ceremony'], 'credential': {'id': b64(self.device.id)}}).status_code, 400)

    def test_csrf_and_post_are_required(self):
        self.assertEqual(self.client.get(reverse('passkey_auth_options')).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(self.post('passkey_register_options', {'password': self.password}, strict).status_code, 403)

    def test_non_owner_and_unsafe_next_cannot_gain_access(self):
        outsider = get_user_model().objects.create_user(username='outsider', password=self.password)
        other = Client(); other.force_login(outsider)
        self.assertEqual(other.get(reverse('login_security')).status_code, 403)
        self.enroll(); self.client.logout()
        self.client.post(reverse('login')+'?next=https://evil.test/', {'username': self.user.username, 'password': self.password})
        self.assertEqual(self.client.session[PENDING]['next'], reverse('home'))

    def test_vercel_uses_platform_ip_and_secure_cookie(self):
        with override_settings(IS_VERCEL=True, DEBUG=False):
            from config.passkeys import network_hash, remember
            from django.test import RequestFactory
            from django.http import JsonResponse
            request = RequestFactory().get('/', HTTP_X_VERCEL_FORWARDED_FOR='203.0.113.5', HTTP_X_FORWARDED_FOR='192.0.2.7')
            self.assertEqual(network_hash(request), digest('203.0.113.5', 'network'))
            profile = LoginSecurity.objects.create(user=self.user)
            response = JsonResponse({'ok': True}); remember(request, response, self.user, profile)
            self.assertTrue(response.cookies[COOKIE]['secure'])
            request.META['HTTP_X_VERCEL_FORWARDED_FOR'] = 'unknown'
            self.assertEqual(network_hash(request), '')


@override_settings(PASSKEY_ORIGIN='http://testserver')
class SecurityBackupTests(TransactionTestCase):
    def test_data_backup_excludes_security_and_restore_preserves_it(self):
        from .portable_backups import create_data_backup, validate_data_backup, restore_validated_data
        user = get_user_model().objects.create_user(username='backup-security', password='Fixture-backup-pass-392!')
        profile = LoginSecurity.objects.create(user=user, enabled=True)
        Passkey.objects.create(user=user, credential_id='fixture', public_key=b'fixture-public')
        archive, _ = create_data_backup()
        with zipfile.ZipFile(BytesIO(archive)) as files:
            text = files.read('data.json').decode()
            for model in ['loginsecurity', 'passkey', 'trustedbrowser', 'recoverycode']:
                self.assertNotIn('tasks.' + model, text)
        objects, _ = validate_data_backup(archive)
        restore_validated_data(objects)
        profile.refresh_from_db()
        self.assertTrue(profile.enabled)
        self.assertEqual(Passkey.objects.get().credential_id, 'fixture')
