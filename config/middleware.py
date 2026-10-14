import ipaddress
import logging
import secrets

from django.conf import settings
from django.http import HttpResponseForbidden
from django.http.response import HttpResponseServerError
from django.utils.cache import add_never_cache_headers


error_logger = logging.getLogger("dugyeon.errors")


class LocalOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote_address = request.META.get("REMOTE_ADDR", "")
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("HTTP_FORWARDED")
        try:
            is_local = ipaddress.ip_address(remote_address).is_loopback and not forwarded
        except (TypeError, ValueError):
            is_local = False
        if not is_local:
            return HttpResponseForbidden("로컬 접속만 허용됩니다.")
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = settings.CSP_POLICY
        response["Referrer-Policy"] = settings.REFERRER_POLICY
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        if (getattr(request, "user", None) and request.user.is_authenticated) or request.path.startswith("/accounts/"):
            add_never_cache_headers(response)
        return response


class OwnerAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            from .access import is_owner
            if not is_owner(request.user) and request.path != "/accounts/logout/":
                return HttpResponseForbidden("이 계정으로 개인 기록에 접근할 수 없습니다.")
        return self.get_response(request)


class ErrorIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception:
            error_id = secrets.token_hex(8)
            error_logger.error("request_error_id=%s path=%s", error_id, request.path)
            response = HttpResponseServerError(f"요청을 처리하지 못했습니다. 오류 ID: {error_id}")
            response["X-Error-ID"] = error_id
        if response.status_code >= 400 and "X-Error-ID" not in response:
            response["X-Error-ID"] = secrets.token_hex(8)
        return response


class DailyBackupBeforeChangeMiddleware:
    WRITE_PREFIXES = ("/tasks/", "/records/", "/categories/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.daily_backup_failed = False
        is_test_client = settings.DEBUG and request.META.get("SERVER_NAME") == "testserver"
        if not is_test_client and request.method == "POST" and request.user.is_authenticated and request.path.startswith(self.WRITE_PREFIXES):
            from tasks.backups import ensure_daily_backup_before_change

            request.daily_backup_failed = not ensure_daily_backup_before_change()
        return self.get_response(request)
