from django.utils import timezone
from django.conf import settings


def backup_warning(request):
    if settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
        return {
            "daily_backup_warning": False,
            "postgres_backup": True,
            "provider_backups_configured": settings.DATABASE_PROVIDER_BACKUPS_CONFIGURED,
        }
    if not settings.AUTOMATIC_DATABASE_BACKUPS:
        return {"daily_backup_warning": False}
    if getattr(request, "daily_backup_failed", False):
        return {"daily_backup_warning": True}
    try:
        from tasks.models import BackupLog

        latest = BackupLog.objects.filter(backup_type=BackupLog.BackupType.DAILY).first()
        warning = bool(
            latest
            and latest.status == BackupLog.Status.FAILED
            and timezone.localtime(latest.started_at).date() == timezone.localdate()
        )
    except Exception:
        warning = False
    return {"daily_backup_warning": warning}


def backup_reminder(request):
    if not request.user.is_authenticated:
        return {}
    from tasks.reminders import claim_popup, reminder_context
    context = reminder_context(request.user)
    # Give the backup/download workflow time without recording it as completed.
    quiet_until = request.session.get("backup_reminder_quiet_until", 0)
    quiet = isinstance(quiet_until, (int, float)) and quiet_until > timezone.now().timestamp()
    context["backup_reminder_show_notice"] = (
        context["backup_reminder_due"]
        and not quiet
        and not request.path.startswith(("/accounts/", "/settings/backups/"))
    )
    context["backup_reminder_show_popup"] = (
        context["backup_reminder_show_notice"]
        and request.method == "GET"
        and getattr(request.resolver_match, "url_name", None) in {"home", "day"}
        and request.GET.get("fragment") != "1"
        and request.headers.get("HX-Request", "").lower() != "true"
        and context["backup_popup_pending"]
    )
    if context["backup_reminder_show_popup"]:
        context["backup_reminder_show_popup"] = claim_popup(request.user)
    return context
