import calendar
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import BackupReminder, DailyPhoto


def six_months_after(value):
    local = timezone.localtime(value)
    month_index = local.year * 12 + local.month - 1 + 6
    year, month = divmod(month_index, 12)
    month += 1
    return local.replace(year=year, month=month, day=min(local.day, calendar.monthrange(year, month)[1]))


def reminder_context(user, preference=None, now=None):
    preference = preference or BackupReminder.objects.filter(user=user).first()
    now = now or timezone.now()
    last_data = preference.last_confirmed_at if preference else None
    last_new = preference.last_new_photos_confirmed_at if preference else None
    last_full = preference.last_full_photos_confirmed_at if preference else None
    cutoff = preference.photos_covered_until if preference else None
    interval = preference.interval_days if preference else 7
    photos = DailyPhoto.objects.all()
    first_photo = photos.order_by("created_at").values_list("created_at", flat=True).first()
    pending_photos = photos.filter(created_at__gt=cutoff) if cutoff else photos
    pending_count = pending_photos.count()

    def cycle(kind, label, confirmed, due_at, warning_at, critical_at, active=True):
        due = active and now >= due_at
        severity = "critical" if due and now >= critical_at else "warning" if due and now >= warning_at else "due" if due else "ready"
        return {"kind": kind, "label": label, "confirmed_at": confirmed,
                "due_at": due_at if active else None, "due": due, "severity": severity,
                "popup_key": f"{confirmed.isoformat() if confirmed else 'first'}:{severity}"}

    data_anchor = last_data or user.date_joined
    data_due = last_data + timedelta(days=interval) if last_data else data_anchor
    new_anchor = last_new or first_photo or now
    new_due = new_anchor + timedelta(days=30)
    full_due = six_months_after(last_full) if last_full else first_photo or now
    cycles = [
        cycle("data", "일정·기록", last_data, data_due,
              data_anchor + timedelta(days=max(interval, 14)), data_anchor + timedelta(days=max(interval, 30))),
        cycle("new", "새 사진", last_new, new_due, new_due + timedelta(days=14), new_due + timedelta(days=30), pending_count > 0),
        cycle("full", "전체 사진", last_full, full_due, full_due + timedelta(days=14), full_due + timedelta(days=30), first_photo is not None),
    ]
    snoozed = bool(preference and preference.snoozed_until and now < preference.snoozed_until)
    due_cycles = [item for item in cycles if item["due"]]
    seen = preference.popup_seen if preference else {}
    # An explicit one-day snooze requests another popup, even for an already seen stage.
    snooze_expired = bool(preference and preference.snoozed_until and not snoozed)
    popup_pending = bool(due_cycles and (snooze_expired or any(seen.get(item["kind"]) != item["popup_key"] for item in due_cycles)))
    return {"backup_cycles": cycles, "backup_due_cycles": due_cycles,
            "backup_reminder_due": bool(due_cycles) and not snoozed,
            "backup_popup_pending": popup_pending and not snoozed,
            "backup_critical": any(item["severity"] == "critical" for item in due_cycles),
            "backup_interval_days": interval, "backup_last_confirmed_at": last_data,
            "backup_due_at": data_due, "has_photos": first_photo is not None,
            "backup_pending_photo_count": pending_count}


def claim_popup(user):
    # Share stage delivery between devices and serialize simultaneous page requests.
    with transaction.atomic():
        BackupReminder.objects.get_or_create(user=user)
        preference = BackupReminder.objects.select_for_update().get(user=user)
        context = reminder_context(user, preference)
        if not context["backup_popup_pending"]:
            return False
        seen = dict(preference.popup_seen)
        for item in context["backup_due_cycles"]:
            seen[item["kind"]] = item["popup_key"]
        preference.popup_seen = seen
        preference.snoozed_until = None
        preference.save(update_fields=["popup_seen", "snoozed_until"])
        return True
