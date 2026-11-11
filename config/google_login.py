"""Owner-only Google OIDC, explicitly linked after password and passkey proof."""
import secrets
from datetime import timedelta

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from joserfc.errors import JoseError
from requests.exceptions import RequestException

from tasks.models import GoogleAccount, GoogleLoginAttempt, LoginSecurity, Passkey, TrustedBrowser
from . import passkeys
from .access import is_owner


def configured():
    return bool(settings.GOOGLE_LOGIN_ENABLED and settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def google_client():
    # Fixed Google endpoints avoid discovery overrides. Authlib handles PKCE,
    # state, nonce and signed ID-token (issuer/audience/expiry) verification.
    return OAuth().register(
        "google", client_id=settings.GOOGLE_CLIENT_ID, client_secret=settings.GOOGLE_CLIENT_SECRET,
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        access_token_url="https://oauth2.googleapis.com/token",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuer="https://accounts.google.com",
        id_token_signing_alg_values_supported=["RS256"],
        client_kwargs={"scope": "openid email", "code_challenge_method": "S256",
                       "token_endpoint_auth_method": "client_secret_post", "timeout": 10},
    )


def safe_next(value):
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        if len(value) <= 2048 and not any(character in value for character in ("\\", "\r", "\n")):
            return value
    return reverse("home")


def error_page(request, message, *, management=False, status=400):
    response = render(request, "google_login_error.html", {
        "google_error": message,
        "return_url": reverse("login_security" if management else "login"),
    }, status=status)
    response["Referrer-Policy"] = "no-referrer"
    return response


def management_profile(request):
    profile = LoginSecurity.objects.select_for_update().get(user=request.user, enabled=True)
    if not has_passkey(request.user):
        raise passkeys.SecurityFailure("먼저 패스키를 등록하고 추가 인증을 켜 주세요.")
    passkeys.require_management(request, request.user, profile, request.POST)
    return profile


def has_passkey(user):
    return Passkey.objects.filter(user=user, revoked_at__isnull=True).exists()


def start(request, purpose, profile=None):
    if not configured():
        return error_page(request, "Google 로그인을 준비하고 있습니다. 기존 로그인으로 접속해 주세요.",
                          management=purpose == "link", status=503)
    passkeys.relying_party(request)
    now = timezone.now()
    network = passkeys.network_hash(request)
    if not network:
        raise passkeys.SecurityFailure("접속 정보를 확인하지 못했습니다. 다시 시도해 주세요.")
    GoogleLoginAttempt.objects.filter(expires_at__lt=now - timedelta(hours=1)).delete()
    if GoogleLoginAttempt.objects.filter(network_hash=network, created_at__gt=now - timedelta(minutes=10)).count() >= 20:
        return error_page(request, "로그인 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
                          management=purpose == "link", status=429)
    request.session.cycle_key()
    state = secrets.token_urlsafe(32)
    GoogleLoginAttempt.objects.create(
        state_hash=passkeys.digest(state, "google-state"),
        session_hash=passkeys.digest(request.session.session_key, "google-session"),
        network_hash=network, purpose=purpose,
        user=request.user if purpose == "link" else None,
        auth_hash=request.user.get_session_auth_hash() if purpose == "link" else "",
        generation=profile.generation if profile else None,
        next_path=safe_next(request.POST.get("next")), expires_at=now + timedelta(minutes=5),
    )
    return google_client().authorize_redirect(
        request, settings.PASSKEY_ORIGIN.rstrip("/") + reverse("google_callback"),
        state=state, prompt="select_account", access_type="online",
    )


@require_POST
@never_cache
def login_start(request):
    if request.user.is_authenticated:
        return redirect("home")
    try:
        return start(request, "login")
    except (passkeys.SecurityFailure, OAuthError, RequestException):
        return error_page(request, "Google 로그인을 시작하지 못했습니다. 운영 주소에서 다시 시도해 주세요.")


@login_required
@require_POST
@never_cache
def link_start(request):
    try:
        if not is_owner(request.user):
            raise passkeys.SecurityFailure("이 계정으로 연결할 수 없습니다.")
        with transaction.atomic():
            profile = management_profile(request)
            if GoogleAccount.objects.filter(user=request.user).exists():
                raise passkeys.SecurityFailure("이미 연결된 Google 계정이 있습니다. 변경하려면 먼저 연결을 해제해 주세요.")
        return start(request, "link", profile)
    except (passkeys.SecurityFailure, LoginSecurity.DoesNotExist) as error:
        message = str(error) if isinstance(error, passkeys.SecurityFailure) else "먼저 패스키를 등록하고 추가 인증을 켜 주세요."
        return error_page(request, message, management=True)
    except (OAuthError, RequestException):
        return error_page(request, "Google 연결을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.", management=True)


def consume_attempt(request):
    state = request.GET.get("state", "")
    if not 20 <= len(state) <= 128:
        raise passkeys.SecurityFailure("로그인 요청이 만료되었거나 이미 사용되었습니다. 다시 시작해 주세요.")
    attempt = GoogleLoginAttempt.objects.filter(
        state_hash=passkeys.digest(state, "google-state"),
        session_hash=passkeys.digest(request.session.session_key or "", "google-session"),
        consumed_at__isnull=True, expires_at__gt=timezone.now(),
    ).first()
    if not attempt or GoogleLoginAttempt.objects.filter(pk=attempt.pk, consumed_at__isnull=True,
            expires_at__gt=timezone.now()).update(consumed_at=timezone.now()) != 1:
        raise passkeys.SecurityFailure("로그인 요청이 만료되었거나 이미 사용되었습니다. 다시 시작해 주세요.")
    return attempt


def verified_identity(request):
    token = google_client().authorize_access_token(request, leeway=30, claims_options={
        "iss": {"essential": True, "values": ["https://accounts.google.com", "accounts.google.com"]},
        "sub": {"essential": True}, "email": {"essential": True}, "email_verified": {"essential": True},
    })
    identity = token.get("userinfo")
    if not token.get("id_token") or not identity or identity.get("email_verified") is not True:
        raise passkeys.SecurityFailure("Google 계정을 확인하지 못했습니다.")
    subject, email = identity.get("sub"), identity.get("email")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255 or not isinstance(email, str) or len(email) > 254:
        raise passkeys.SecurityFailure("Google 계정을 확인하지 못했습니다.")
    validate_email(email)
    # Access/refresh/ID tokens are neither persisted nor placed in the session.
    return subject, email


def complete_link(request, attempt, subject, email):
    with transaction.atomic():
        if not request.user.is_authenticated or request.user.pk != attempt.user_id:
            raise passkeys.SecurityFailure("기존 계정으로 로그인한 뒤 다시 연결해 주세요.")
        user = get_user_model().objects.select_for_update().get(pk=attempt.user_id, is_active=True)
        profile = LoginSecurity.objects.select_for_update().get(user=user, enabled=True)
        if (not is_owner(user) or str(profile.generation) != str(attempt.generation)
            or not constant_time_compare(attempt.auth_hash, user.get_session_auth_hash())
            or not passkeys.recent_verification(request, user, profile) or not has_passkey(user)):
            raise passkeys.SecurityFailure("본인 확인이 만료되었습니다. 패스키로 확인한 뒤 다시 연결해 주세요.")
        if GoogleAccount.objects.filter(user=user).exists() or GoogleAccount.objects.filter(subject=subject).exists():
            raise passkeys.SecurityFailure("이미 연결된 계정입니다. 기존 연결을 먼저 확인해 주세요.")
        GoogleAccount.objects.create(user=user, subject=subject, email=email)
    messages.success(request, "Google 계정을 연결했습니다. 기존 일정과 기록을 그대로 사용할 수 있습니다.")
    return redirect("login_security")


@require_GET
@never_cache
def callback(request):
    attempt = None
    try:
        if not configured():
            raise passkeys.SecurityFailure("Google 로그인을 준비하고 있습니다. 기존 로그인으로 접속해 주세요.")
        passkeys.relying_party(request)
        attempt = consume_attempt(request)
        subject, email = verified_identity(request)
        if attempt.purpose == "link":
            response = complete_link(request, attempt, subject, email)
        else:
            account = GoogleAccount.objects.select_related("user").get(subject=subject, user__is_active=True)
            if not is_owner(account.user) or not LoginSecurity.objects.filter(user=account.user, enabled=True).exists():
                raise passkeys.SecurityFailure("연결된 계정의 추가 인증 설정을 먼저 확인해 주세요.")
            response = passkeys.begin_owner_login(request, account.user, attempt.next_path, google_account=account.pk)
        response["Referrer-Policy"] = "no-referrer"
        return response
    except (passkeys.SecurityFailure, ValidationError, OAuthError, JoseError, RequestException,
            GoogleAccount.DoesNotExist, LoginSecurity.DoesNotExist, get_user_model().DoesNotExist, IntegrityError):
        return error_page(request,
            "Google 확인을 완료하지 못했습니다. 연결한 본인 계정인지 확인하고 다시 시작해 주세요. 처음 연결할 때는 기존 계정으로 로그인한 뒤 설정에서 진행해 주세요.",
            management=bool(attempt and attempt.purpose == "link"))


@login_required
@require_POST
@never_cache
def unlink(request):
    try:
        if not is_owner(request.user):
            raise passkeys.SecurityFailure("이 계정으로 연결을 변경할 수 없습니다.")
        passkeys.relying_party(request)
        with transaction.atomic():
            profile = management_profile(request)
            GoogleAccount.objects.filter(user=request.user).delete()
            # Revoke Google sessions without invalidating saved recovery codes.
            request.session.pop("dugyeon_google_account", None)
            TrustedBrowser.objects.filter(user=request.user, revoked_at__isnull=True).update(revoked_at=timezone.now())
            passkeys.mark_verified(request, request.user, profile, recent=True)
        response = redirect("login_security")
        response.delete_cookie(passkeys.COOKIE, path="/", samesite="Lax")
        messages.success(request, "Google 연결을 해제했습니다. 기존 비밀번호와 패스키로 로그인할 수 있습니다.")
        return response
    except (passkeys.SecurityFailure, LoginSecurity.DoesNotExist) as error:
        message = str(error) if isinstance(error, passkeys.SecurityFailure) else "추가 인증 설정을 확인해 주세요."
        return error_page(request, message, management=True)
