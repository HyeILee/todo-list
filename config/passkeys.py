"""Password-first, conditional WebAuthn verification for the private owner account."""
import ipaddress
import json
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from webauthn import (base64url_to_bytes, generate_authentication_options,
    generate_registration_options, options_to_json, verify_authentication_response,
    verify_registration_response)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement)

from .access import is_owner
from .forms import OwnerAuthenticationForm
from tasks.models import LoginSecurity, Passkey, PasskeyChallenge, RecoveryCode, TrustedBrowser

COOKIE = "dugyeon_trusted_browser"
PENDING = "dugyeon_pending_login"
BACKEND = "django.contrib.auth.backends.ModelBackend"


class SecurityFailure(Exception):
    pass


def digest(value, purpose):
    return salted_hmac("dugyeon." + purpose, value, algorithm="sha256").hexdigest()


def network_hash(request):
    # Vercel overwrites this header; outside Vercel, ignore all forwarded headers.
    raw = request.META.get("HTTP_X_VERCEL_FORWARDED_FOR", "") if settings.IS_VERCEL else request.META.get("REMOTE_ADDR", "")
    try:
        return digest(ipaddress.ip_address(raw.strip()).compressed, "network")
    except (ValueError, AttributeError):
        return ""


def browser_hash(request):
    return digest(request.META.get("HTTP_USER_AGENT", "")[:1024], "browser")


def relying_party(request):
    origin = settings.PASSKEY_ORIGIN.rstrip("/")
    parts = urlsplit(origin)
    if request.get_host() != parts.netloc or parts.path or parts.query or parts.fragment:
        raise SecurityFailure("운영 주소에서 다시 열어 주세요.")
    return parts.hostname, origin


def payload(request):
    if len(request.body) > 65536:
        raise SecurityFailure("요청이 너무 큽니다.")
    try:
        data = json.loads(request.body) if request.content_type == "application/json" else request.POST.dict()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError):
        raise SecurityFailure("요청을 확인해 주세요.")


def fail(message="본인 확인을 완료하지 못했습니다. 다시 시도해 주세요.", status=400):
    return JsonResponse({"error": message}, status=status)


def mark_verified(request, user, profile, *, recent=False):
    request.session["dugyeon_mfa_generation"] = str(profile.generation)
    if recent:
        request.session["dugyeon_mfa_at"] = timezone.now().timestamp()
    request.session["dugyeon_mfa_auth_hash"] = user.get_session_auth_hash()


def recent_verification(request, user, profile):
    return (request.session.get("dugyeon_mfa_generation") == str(profile.generation)
        and constant_time_compare(request.session.get("dugyeon_mfa_auth_hash", ""), user.get_session_auth_hash())
        and 0 <= timezone.now().timestamp() - request.session.get("dugyeon_mfa_at", 0) <= 300)


def require_password(request, user, data):
    if authenticate(request=request, username=user.get_username(), password=str(data.get("password", ""))) != user:
        raise SecurityFailure("현재 비밀번호를 확인해 주세요.")


def require_management(request, user, profile, data):
    if profile.enabled and not recent_verification(request, user, profile):
        raise SecurityFailure("먼저 등록한 패스키로 본인 확인을 해 주세요.")
    require_password(request, user, data)


def trusted(request, user, profile):
    token = request.COOKIES.get(COOKIE, "")
    network = network_hash(request)
    if not token or len(token) > 128 or not network:
        return False
    return TrustedBrowser.objects.filter(user=user, token_hash=digest(token, "trust"),
        generation=profile.generation, auth_hash=user.get_session_auth_hash(),
        network_hash=network, browser_hash=browser_hash(request), revoked_at__isnull=True,
        expires_at__gt=timezone.now()).exists()


def remember(request, response, user, profile):
    if not network_hash(request):
        return
    token = secrets.token_urlsafe(32)
    TrustedBrowser.objects.create(user=user, token_hash=digest(token, "trust"),
        generation=profile.generation, auth_hash=user.get_session_auth_hash(),
        network_hash=network_hash(request), browser_hash=browser_hash(request),
        expires_at=timezone.now() + timedelta(days=30))
    response.set_cookie(COOKIE, token, max_age=30 * 86400, secure=not settings.DEBUG,
        httponly=True, samesite="Lax", path="/")


def blocked(profile):
    return profile.blocked_until and profile.blocked_until > timezone.now()


def note_failure(profile):
    profile.failures += 1
    if profile.failures >= 5:
        profile.blocked_until = timezone.now() + timedelta(minutes=10)
        profile.failures = 0
    profile.save(update_fields=["failures", "blocked_until"])


def clear_failures(profile):
    profile.failures = 0
    profile.blocked_until = None
    profile.save(update_fields=["failures", "blocked_until"])


def pending_user(request):
    pending = request.session.get(PENDING, {})
    try:
        user = get_user_model().objects.get(pk=pending["user"], is_active=True)
        profile = LoginSecurity.objects.get(user=user, enabled=True)
        if (not is_owner(user) or not 0 <= timezone.now().timestamp() - pending["at"] <= 300
            or pending["generation"] != str(profile.generation)
            or not constant_time_compare(pending["auth_hash"], user.get_session_auth_hash())):
            raise ValueError
        return user, profile
    except (KeyError, TypeError, ValueError, get_user_model().DoesNotExist, LoginSecurity.DoesNotExist):
        request.session.pop(PENDING, None)
        raise SecurityFailure("로그인 시간이 지났습니다. 비밀번호부터 다시 입력해 주세요.")


class ConditionalLoginView(LoginView):
    template_name = "login.html"
    authentication_form = OwnerAuthenticationForm

    def form_valid(self, form):
        user = form.get_user()
        profile = LoginSecurity.objects.filter(user=user, enabled=True).first()
        if profile and not trusted(self.request, user, profile):
            self.request.session.cycle_key()
            self.request.session[PENDING] = {"user": user.pk, "at": timezone.now().timestamp(),
                "auth_hash": user.get_session_auth_hash(), "generation": str(profile.generation),
                "next": self.get_success_url()}
            return redirect("passkey_challenge")
        self.request.session.pop(PENDING, None)
        response = super().form_valid(form)
        if profile:
            mark_verified(self.request, user, profile)
        return response


class VerifiedLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.path != reverse("logout"):
            profile = LoginSecurity.objects.filter(user=request.user, enabled=True).first()
            if profile and (request.session.get("dugyeon_mfa_generation") != str(profile.generation)
                or not constant_time_compare(request.session.get("dugyeon_mfa_auth_hash", ""), request.user.get_session_auth_hash())):
                logout(request)
                return redirect("login")
        return self.get_response(request)


@login_required
@never_cache
def security_page(request):
    profile, _ = LoginSecurity.objects.get_or_create(user=request.user)
    rp_ok = True
    try:
        relying_party(request)
    except SecurityFailure:
        rp_ok = False
    return render(request, "passkey_settings.html", {"security": profile,
        "passkeys": Passkey.objects.filter(user=request.user, revoked_at__isnull=True),
        "recovery_remaining": RecoveryCode.objects.filter(user=request.user, generation=profile.generation, used_at__isnull=True).count(),
        "passkey_origin": settings.PASSKEY_ORIGIN, "passkey_rp_ok": rp_ok})


@never_cache
def challenge_page(request):
    try:
        pending_user(request)
    except SecurityFailure:
        return redirect("login")
    return render(request, "passkey_challenge.html")


def new_challenge(request, user, profile, purpose, options):
    if not request.session.session_key:
        request.session.create()
    now = timezone.now()
    PasskeyChallenge.objects.filter(expires_at__lt=now).delete()
    row = PasskeyChallenge.objects.create(user=user, purpose=purpose,
        session_hash=digest(request.session.session_key, "session"),
        auth_hash=user.get_session_auth_hash(), generation=profile.generation,
        challenge=options.challenge, expires_at=now + timedelta(minutes=5))
    return {"ceremony": str(row.pk), "options": json.loads(options_to_json(options))}


def consume_challenge(request, user, profile, data, purpose):
    try:
        row = PasskeyChallenge.objects.select_for_update().get(pk=data.get("ceremony"), user=user,
            purpose=purpose, consumed_at__isnull=True, expires_at__gt=timezone.now(),
            generation=profile.generation, session_hash=digest(request.session.session_key or "", "session"),
            auth_hash=user.get_session_auth_hash())
    except (PasskeyChallenge.DoesNotExist, ValueError, ValidationError):
        raise SecurityFailure("본인 확인 요청이 만료되었거나 이미 사용되었습니다. 다시 시작해 주세요.")
    row.consumed_at = timezone.now()
    row.save(update_fields=["consumed_at"])
    return bytes(row.challenge)


@require_POST
@never_cache
def authentication_options(request):
    try:
        data = payload(request)
        purpose = "manage" if request.user.is_authenticated else "login"
        user, profile = (request.user, LoginSecurity.objects.get(user=request.user)) if purpose == "manage" else pending_user(request)
        if purpose == "manage" and not profile.enabled:
            require_password(request, user, data)
        if blocked(profile):
            return fail("확인 시도가 많습니다. 10분 후 다시 시도해 주세요.", 429)
        rp, _ = relying_party(request)
        keys = Passkey.objects.filter(user=user, revoked_at__isnull=True)
        if not keys.exists():
            raise SecurityFailure("등록한 패스키가 없습니다. 복구 코드를 이용해 주세요.")
        options = generate_authentication_options(rp_id=rp,
            allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id)) for k in keys],
            user_verification=UserVerificationRequirement.REQUIRED)
        return JsonResponse(new_challenge(request, user, profile, purpose, options))
    except (SecurityFailure, LoginSecurity.DoesNotExist) as error:
        return fail(str(error))


def finish_login(request, user, profile, data):
    destination = request.session.get(PENDING, {}).get("next", reverse("home"))
    request.session.pop(PENDING, None)
    login(request, user, backend=BACKEND)
    mark_verified(request, user, profile, recent=True)
    response = JsonResponse({"ok": True, "next": destination})
    if data.get("remember") is True:
        remember(request, response, user, profile)
    return response


@require_POST
@never_cache
def authentication_verify(request):
    try:
        data = payload(request)
        purpose = "manage" if request.user.is_authenticated else "login"
        user, _ = (request.user, None) if purpose == "manage" else pending_user(request)
        rp, origin = relying_party(request)
        with transaction.atomic():
            profile = LoginSecurity.objects.select_for_update().get(user=user)
            if blocked(profile):
                return fail("확인 시도가 많습니다. 10분 후 다시 시도해 주세요.", 429)
            challenge = consume_challenge(request, user, profile, data, purpose)
            try:
                credential = data["credential"]
                key = Passkey.objects.select_for_update().get(user=user, credential_id=credential["id"], revoked_at__isnull=True)
                result = verify_authentication_response(credential=credential, expected_challenge=challenge,
                    expected_rp_id=rp, expected_origin=origin, credential_public_key=bytes(key.public_key),
                    credential_current_sign_count=key.sign_count, require_user_verification=True)
                if result.credential_id != base64url_to_bytes(key.credential_id):
                    raise ValueError
                key.sign_count = result.new_sign_count
                key.last_used_at = timezone.now()
                key.save(update_fields=["sign_count", "last_used_at"])
            except (KeyError, TypeError, ValueError, Passkey.DoesNotExist, WebAuthnException):
                note_failure(profile)
                return fail()
            clear_failures(profile)
            if purpose == "login":
                return finish_login(request, user, profile, data)
            mark_verified(request, user, profile, recent=True)
            if not profile.enabled:
                return enrollment_result(request, user, profile)
            return JsonResponse({"ok": True})
    except (SecurityFailure, LoginSecurity.DoesNotExist) as error:
        return fail(str(error))


@login_required
@require_POST
@never_cache
def registration_options(request):
    try:
        data = payload(request)
        profile, _ = LoginSecurity.objects.get_or_create(user=request.user)
        require_management(request, request.user, profile, data)
        rp, _ = relying_party(request)
        keys = Passkey.objects.filter(user=request.user, revoked_at__isnull=True)
        if keys.count() >= 5:
            raise SecurityFailure("패스키는 최대 5개까지 등록할 수 있습니다.")
        options = generate_registration_options(rp_id=rp, rp_name="To-do List",
            user_id=digest(str(request.user.pk), "passkey-user").encode(), user_name=request.user.get_username(),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id)) for k in keys],
            authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED))
        return JsonResponse(new_challenge(request, request.user, profile, "register", options))
    except SecurityFailure as error:
        return fail(str(error))


def recovery_codes(user, profile):
    RecoveryCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    codes = [secrets.token_hex(16) for _ in range(10)]
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, generation=profile.generation,
        code_hash=digest(f"{user.pk}:{profile.generation}:{code}", "recovery")) for code in codes])
    return ["-".join(code[i:i + 8] for i in range(0, 32, 8)) for code in codes]


def enrollment_result(request, user, profile):
    codes = recovery_codes(user, profile)
    request.session["dugyeon_enroll_proof"] = {"generation": str(profile.generation),
        "auth_hash": user.get_session_auth_hash(), "at": timezone.now().timestamp()}
    return JsonResponse({"ok": True, "codes": codes})


@login_required
@require_POST
@never_cache
def registration_verify(request):
    try:
        data = payload(request)
        rp, origin = relying_party(request)
        with transaction.atomic():
            profile = LoginSecurity.objects.select_for_update().get(user=request.user)
            if profile.enabled and not recent_verification(request, request.user, profile):
                raise SecurityFailure("먼저 등록한 패스키로 본인 확인을 해 주세요.")
            challenge = consume_challenge(request, request.user, profile, data, "register")
            try:
                result = verify_registration_response(credential=data["credential"], expected_challenge=challenge,
                    expected_rp_id=rp, expected_origin=origin, require_user_verification=True)
            except (KeyError, TypeError, ValueError, WebAuthnException):
                return fail()
            credential_id = bytes_to_base64url(result.credential_id)
            if not 1 <= len(credential_id) <= 1024:
                return fail()
            if Passkey.objects.filter(credential_id=credential_id).exists():
                return fail("이미 등록한 패스키입니다.")
            if Passkey.objects.filter(user=request.user, revoked_at__isnull=True).count() >= 5:
                return fail("패스키는 최대 5개까지 등록할 수 있습니다.")
            Passkey.objects.create(user=request.user, credential_id=credential_id, public_key=result.credential_public_key,
                sign_count=result.sign_count, label=str(data.get("label") or "내 패스키")[:60])
            if profile.enabled:
                return JsonResponse({"ok": True, "next": reverse("login_security")})
            return enrollment_result(request, request.user, profile)
    except (SecurityFailure, LoginSecurity.DoesNotExist) as error:
        return fail(str(error))


@login_required
@require_POST
@never_cache
def security_action(request):
    try:
        data = payload(request)
        with transaction.atomic():
            profile = LoginSecurity.objects.select_for_update().get(user=request.user)
            action = data.get("action")
            if action == "enable":
                proof = request.session.get("dugyeon_enroll_proof", {})
                if (profile.enabled or data.get("codes_saved") is not True or proof.get("generation") != str(profile.generation)
                    or not constant_time_compare(proof.get("auth_hash", ""), request.user.get_session_auth_hash())
                    or not 0 <= timezone.now().timestamp() - proof.get("at", 0) <= 600
                    or not Passkey.objects.filter(user=request.user, revoked_at__isnull=True).exists()):
                    raise SecurityFailure("패스키 등록과 복구 코드 보관을 먼저 완료해 주세요.")
                profile.enabled = True
                profile.save(update_fields=["enabled"])
                request.session.pop("dugyeon_enroll_proof", None)
                mark_verified(request, request.user, profile, recent=True)
                response = JsonResponse({"ok": True, "next": reverse("login_security")})
                if data.get("remember") is True:
                    remember(request, response, request.user, profile)
                return response
            require_management(request, request.user, profile, data)
            if action == "codes":
                return JsonResponse({"ok": True, "codes": recovery_codes(request.user, profile)})
            if action not in {"forget", "disable"}:
                raise SecurityFailure("요청을 확인해 주세요.")
            TrustedBrowser.objects.filter(user=request.user, revoked_at__isnull=True).update(revoked_at=timezone.now())
            if action == "disable":
                profile.enabled = False
                profile.generation = uuid.uuid4()
                profile.save(update_fields=["enabled", "generation"])
                Passkey.objects.filter(user=request.user, revoked_at__isnull=True).update(revoked_at=timezone.now())
                RecoveryCode.objects.filter(user=request.user, used_at__isnull=True).update(used_at=timezone.now())
            response = JsonResponse({"ok": True, "next": reverse("login_security")})
            response.delete_cookie(COOKIE, path="/", samesite="Lax")
            return response
    except (SecurityFailure, LoginSecurity.DoesNotExist) as error:
        return fail(str(error))


@require_POST
@never_cache
def recovery_verify(request):
    try:
        data = payload(request)
        user, _ = pending_user(request)
        with transaction.atomic():
            profile = LoginSecurity.objects.select_for_update().get(user=user, enabled=True)
            if blocked(profile):
                return fail("확인 시도가 많습니다. 10분 후 다시 시도해 주세요.", 429)
            code = str(data.get("code", "")).replace("-", "").replace(" ", "").lower()
            code_hash = digest(f"{user.pk}:{profile.generation}:{code}", "recovery")
            row = RecoveryCode.objects.select_for_update().filter(user=user, generation=profile.generation,
                code_hash=code_hash, used_at__isnull=True).first()
            if not row or len(code) != 32:
                note_failure(profile)
                return fail("복구 코드를 확인해 주세요.")
            row.used_at = timezone.now()
            row.save(update_fields=["used_at"])
            clear_failures(profile)
            # Recovery grants one verified session, never a persistent trusted browser.
            return finish_login(request, user, profile, {"remember": False})
    except (SecurityFailure, LoginSecurity.DoesNotExist) as error:
        return fail(str(error))
