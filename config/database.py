"""Database URL parsing without including credentials in error messages."""
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured


def postgres_database(url, *, debug=False):
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError
        if parsed.fragment or set(query) - {"sslmode", "sslrootcert", "channel_binding"}:
            raise ValueError
        if any(len(values) != 1 for values in query.values()):
            raise ValueError
        options = {key: values[0] for key, values in query.items()}
        options.setdefault("sslmode", "prefer" if debug else "require")
        if options["sslmode"] not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError
        if not debug and options["sslmode"] not in {"require", "verify-ca", "verify-full"}:
            raise ValueError
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname,
            "PORT": parsed.port or 5432,
            "OPTIONS": {"connect_timeout": 10, **options},
            "CONN_MAX_AGE": 0,
            "DISABLE_SERVER_SIDE_CURSORS": True,
        }
    except (ValueError, TypeError):
        raise ImproperlyConfigured("DATABASE_URL must be a valid PostgreSQL URL with TLS in production.") from None
