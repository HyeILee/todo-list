import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
# Process environment takes priority over the local UTF-8 .env file.
from dotenv import load_dotenv
IS_VERCEL = os.environ.get("VERCEL") == "1"
if not IS_VERCEL:
    load_dotenv(BASE_DIR / ".env", override=False, encoding="utf-8-sig")


def env_bool(name, default):
    value = os.environ.get(name, default).strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ImproperlyConfigured(f"{name} must be True or False.")
    return value in {"true", "1", "yes"}


DEBUG = env_bool("DJANGO_DEBUG", "false")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY or (not DEBUG and (len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"))):
    raise ImproperlyConfigured("Set a random DJANGO_SECRET_KEY; production requires at least 50 characters.")
ALLOWED_HOSTS = [v.strip() for v in os.environ.get("DJANGO_ALLOWED_HOSTS", os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost" if DEBUG else "")).split(",") if v.strip()]
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", os.environ.get("CSRF_TRUSTED_ORIGINS", "")).split(",") if v.strip()]
REMOTE_OPERATION_APPROVED = env_bool("REMOTE_OPERATION_APPROVED", "false" if DEBUG else "true")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
if REMOTE_OPERATION_APPROVED:
    if DEBUG:
        raise ImproperlyConfigured("Remote operation requires DJANGO_DEBUG=False.")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured("Remote operation requires explicit DJANGO_ALLOWED_HOSTS.")
    if not CSRF_TRUSTED_ORIGINS or any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
        raise ImproperlyConfigured("Remote operation requires HTTPS DJANGO_CSRF_TRUSTED_ORIGINS.")
elif any(host not in LOCAL_HOSTS for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Remote hosts require REMOTE_OPERATION_APPROVED=True.")
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "tasks.apps.TasksConfig", "django.contrib.staticfiles"]
INSTALLED_APPS += ["axes"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "config.middleware.ErrorIdMiddleware", "config.middleware.SecurityHeadersMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "config.middleware.OwnerAccessMiddleware", "axes.middleware.AxesMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
if not REMOTE_OPERATION_APPROVED:
	MIDDLEWARE.insert(3, "config.middleware.LocalOnlyMiddleware")
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "config.context_processors.backup_warning", "config.context_processors.backup_reminder"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASE_PATH = Path(os.environ.get("SQLITE_PATH") or os.environ.get("DATABASE_PATH") or BASE_DIR / "db.sqlite3")
if not DATABASE_PATH.is_absolute():
	DATABASE_PATH = BASE_DIR / DATABASE_PATH
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if IS_VERCEL and (DEBUG or not DATABASE_URL or not REMOTE_OPERATION_APPROVED):
    raise ImproperlyConfigured("Vercel requires PostgreSQL DATABASE_URL and production HTTPS settings.")
if DATABASE_URL:
    from config.database import postgres_database
    DATABASES = {"default": postgres_database(DATABASE_URL, debug=DEBUG)}
else:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATABASE_PATH}}
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", BASE_DIR / "data" / "backups"))
if not BACKUP_DIR.is_absolute():
	BACKUP_DIR = BASE_DIR / BACKUP_DIR
if not DATABASE_URL:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PROVIDER_BACKUPS_CONFIGURED = env_bool("DATABASE_PROVIDER_BACKUPS_CONFIGURED", "false")
AUTOMATIC_DATABASE_BACKUPS = env_bool("AUTOMATIC_DATABASE_BACKUPS", "false")
if AUTOMATIC_DATABASE_BACKUPS and not DATABASE_URL:
    MIDDLEWARE.insert(MIDDLEWARE.index("django.contrib.messages.middleware.MessageMiddleware"), "config.middleware.DailyBackupBeforeChangeMiddleware")
APP_SINGLE_USER = env_bool("APP_SINGLE_USER", "true")
APP_OWNER_USERNAME = os.environ.get("APP_OWNER_USERNAME", "").strip()
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"}, {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}}, {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}, {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "django.contrib.auth.backends.ModelBackend"]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = os.environ.get("APP_TIME_ZONE", "Asia/Seoul")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles"))
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = REMOTE_OPERATION_APPROVED
SECURE_HSTS_SECONDS = 31536000 if REMOTE_OPERATION_APPROVED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = REMOTE_OPERATION_APPROVED
SECURE_HSTS_PRELOAD = REMOTE_OPERATION_APPROVED
if REMOTE_OPERATION_APPROVED:
	SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "same-origin"
CSP_POLICY = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'"
LOGGING = {
	"version": 1,
	"disable_existing_loggers": False,
	"handlers": {"console": {"class": "logging.StreamHandler"}},
	"loggers": {"dugyeon.errors": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}

MIDDLEWARE.insert(MIDDLEWARE.index("config.middleware.OwnerAccessMiddleware") + 1, "config.passkeys.VerifiedLoginMiddleware")
PASSKEY_ORIGIN = os.environ.get("PASSKEY_ORIGIN", "http://localhost:8000" if DEBUG else "https://todo-day-log.vercel.app").strip()

from urllib.parse import urlsplit as _passkey_urlsplit
_passkey_parts = _passkey_urlsplit(PASSKEY_ORIGIN)
if (not _passkey_parts.hostname or _passkey_parts.username or _passkey_parts.password
    or _passkey_parts.path not in {"", "/"} or _passkey_parts.query or _passkey_parts.fragment
    or _passkey_parts.scheme not in ({"http", "https"} if DEBUG else {"https"})):
    raise ImproperlyConfigured("PASSKEY_ORIGIN must be the canonical HTTPS site origin.")
