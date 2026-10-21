from datetime import date, timedelta
import hashlib
from pathlib import PurePath
import sqlite3
import uuid
from django.contrib import messages
from django.contrib.auth import authenticate, logout
from django.contrib.auth.decorators import login_required
from django.db import connections, transaction
from django.db.utils import DatabaseError
from django.core.exceptions import ImproperlyConfigured
from django.core import signing
from django.core.paginator import Paginator
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.urls import reverse
from urllib.parse import quote, urlencode, urlparse
from .exports import csv_for_records, csv_for_tasks, export_filename, workbook_for_records, workbook_for_tasks
from .backups import BackupRestoreError, BackupValidationError, MAX_ARCHIVE_SIZE, create_full_backup, restore_database_archive
from .forms import CategoryForm, DailyRecordForm, RecordFilterForm, TaskFilterForm, TaskForm
from .models import BackupLog, BackupReminder, Category, DailyPhoto, DailyRecord, Holiday, Task, TaskStatusHistory
from .photo_storage import PhotoStorageError, configured as photo_storage_configured, delete_private_photo, download_private_photo, upload_private_photo
from .photo_privacy import PhotoPrivacyError, strip_photo_metadata


@never_cache
@require_POST
def session_activity(request):
    # SessionMiddleware refreshes authenticated sessions on this CSRF-protected POST.
    if not request.user.is_authenticated:
        return JsonResponse({"error": "로그인이 만료됐습니다."}, status=401)
    return JsonResponse({"ok": True})

def week_start(day):
    return day - timedelta(days=day.weekday())

def parse_day(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return timezone.localdate()

TASK_STATUS_ORDER = (Task.Status.PENDING, Task.Status.NOT_DONE, Task.Status.PARTIAL, Task.Status.DONE)


def tasks_in_status_order(tasks, *ordering):
    return tasks.alias(_status_priority=Case(
        *(When(status=status, then=Value(rank)) for rank, status in enumerate(TASK_STATUS_ORDER)),
        default=Value(len(TASK_STATUS_ORDER)), output_field=IntegerField(),
    )).order_by("_status_priority", *ordering, "pk")


def weekly_context(selected, tasks=None):
    start = week_start(selected)
    days = [start + timedelta(days=i) for i in range(7)]
    today = timezone.localdate()
    if tasks is None:
        tasks = tasks_in_status_order(Task.objects.filter(
            task_date__in=days, deleted_at__isnull=True,
        ).select_related("category"), "category__sort_order", "category__name", "sort_order", "created_at")
    by_day = {day: [] for day in days}
    status_counts = {status: 0 for status in TASK_STATUS_ORDER}
    for task in tasks:
        by_day[task.task_date].append(task)
        status_counts[task.status] += 1
    record_dates = set(DailyRecord.objects.filter(record_date__in=days).values_list("record_date", flat=True))
    record_dates.update(DailyPhoto.objects.filter(record_date__in=days).values_list("record_date", flat=True))
    holidays = dict(Holiday.objects.filter(holiday_date__in=days).values_list("holiday_date", "name"))
    week = []
    for day in days:
        day_tasks = by_day[day]
        holiday_name = holidays.get(day, "")
        week.append({
            "date": day,
            "count": len(day_tasks),
            "pending_count": sum(task.status == Task.Status.PENDING for task in day_tasks),
            "tasks": day_tasks,
            "has_record": day in record_dates,
            "selected": day == selected,
            "is_today": day == today,
            "is_past": day < today,
            "calendar_class": "holiday" if holiday_name else ("saturday" if day.weekday() == 5 else ("sunday" if day.weekday() == 6 else "")),
            "holiday_name": holiday_name,
        })
    return {
        "selected_date": selected, "week_start": start, "week_end": days[-1], "week": week,
        "previous_week": start - timedelta(days=7), "next_week": start + timedelta(days=7), "today": today,
        "week_pending_count": sum(day["pending_count"] for day in week),
        "week_past_pending_count": sum(day["pending_count"] for day in week if day["is_past"]),
        "week_task_count": sum(status_counts.values()),
        "week_status_summary": [{"value": status, "label": Task.Status(status).label, "count": status_counts[status]} for status in TASK_STATUS_ORDER],
    }


def weekly_update(selected, request):
    if selected is None:
        return {}
    return {"week_date": selected.isoformat(),
            "week_html": render_to_string("weekly_overview.html", weekly_context(selected), request=request)}


@never_cache
@login_required
def dashboard(request, selected_date=None):
    selected = parse_day(selected_date) if selected_date else timezone.localdate()
    start = week_start(selected)
    days = [start + timedelta(days=i) for i in range(7)]
    tasks = tasks_in_status_order(Task.objects.filter(task_date__in=days, deleted_at__isnull=True).select_related("category"), "category__sort_order", "category__name", "sort_order", "created_at")
    by_day = {day: [] for day in days}
    for task in tasks:
        by_day[task.task_date].append(task)
    grouped = [(category, [t for t in by_day[selected] if t.category_id == category.id]) for category in Category.objects.filter(tasks__task_date=selected, tasks__deleted_at__isnull=True).distinct()]
    record = DailyRecord.objects.filter(record_date=selected).first()
    daily_photos = list(DailyPhoto.objects.filter(record_date=selected).order_by("position", "created_at"))
    context = {**weekly_context(selected, tasks), "grouped_tasks": grouped, "record_form": DailyRecordForm(instance=record), "record": record, "record_version": record.updated_at.isoformat() if record else "", "daily_photos": daily_photos, "photo_slots_remaining": 10 - len(daily_photos), "photo_storage_ready": photo_storage_configured()}
    template = "dashboard_fragment.html" if request.headers.get("HX-Request") == "true" else "dashboard.html"
    return render(request, template, context)


@never_cache
@login_required
@require_POST
def record_save(request, record_date):
    with transaction.atomic():
        latest = DailyRecord.objects.select_for_update().filter(record_date=record_date).first()
        latest_version = latest.updated_at.isoformat() if latest else ""
        form = DailyRecordForm(request.POST, instance=latest)
        # Empty input requests deletion, not an invalid empty model row.
        is_empty = not any(request.POST.get(name, "").strip() for name in form.fields)
        if not is_empty and not form.is_valid():
            return render(request, "record_form_error.html", {"form": form, "record_date": record_date, "record_version": request.POST.get("expected_updated_at", "")}, status=400)
        if request.POST.get("expected_updated_at", "") != latest_version:
            submitted = {name: "" for name in form.fields} if is_empty else form.cleaned_data
            return render(request, "record_edit_conflict.html", {"record_date": record_date, "latest_record": latest, "submitted": submitted}, status=409)
        if is_empty:
            if latest and request.POST.get("confirm_delete") != "yes":
                return render(request, "record_delete_confirm.html", {"record_date": record_date, "record_version": latest_version})
            if latest:
                latest.delete()
                messages.success(request, "개인 기록을 삭제했습니다.")
        else:
            saved = form.save(commit=False)
            saved.record_date = record_date
            saved.save()
            messages.success(request, "개인 기록을 저장했습니다.")
    return redirect("day", selected_date=record_date)


PHOTO_MAX_BYTES = 2_500_000
PHOTO_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
}


def detected_photo_type(content):
    for content_type, (signature, extension) in PHOTO_TYPES.items():
        if content.startswith(signature):
            return content_type, extension
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None, None


@login_required
@require_POST
def photo_upload(request, record_date):
    if not photo_storage_configured():
        return JsonResponse({"error": "사진 저장소가 연결되지 않았습니다."}, status=503)
    uploaded = request.FILES.get("photo")
    if uploaded is None:
        return JsonResponse({"error": "올릴 사진을 선택해 주세요."}, status=400)
    if uploaded.size <= 0 or uploaded.size > PHOTO_MAX_BYTES:
        return JsonResponse({"error": "사진 한 장은 2.5MB 이하여야 합니다."}, status=400)
    category = request.POST.get("category", "")
    valid_categories = {value for value, _label in DailyPhoto.Category.choices}
    if category not in valid_categories:
        return JsonResponse({"error": "사진 분류를 Life, Work, Etc 중에서 선택해 주세요."}, status=400)
    content = uploaded.read(PHOTO_MAX_BYTES + 1)
    content_type, extension = detected_photo_type(content)
    if content_type is None:
        return JsonResponse({"error": "JPEG, PNG, WebP 사진만 올릴 수 있습니다."}, status=400)

    existing_positions = set(DailyPhoto.objects.filter(record_date=record_date).values_list("position", flat=True))
    available_positions = [position for position in range(10) if position not in existing_positions]
    if not available_positions:
        return JsonResponse({"error": "하루에는 사진을 최대 10장까지 올릴 수 있습니다."}, status=409)

    try:
        content = strip_photo_metadata(content, content_type, PHOTO_MAX_BYTES)
    except PhotoPrivacyError as error:
        return JsonResponse({"error": str(error)}, status=400)

    safe_original_name = PurePath((uploaded.name or f"photo{extension}").replace("\\", "/")).name[:255]
    pathname = f"photos/{record_date.isoformat()}/{uuid.uuid4().hex}{extension}"
    try:
        blob_url, blob_pathname = upload_private_photo(pathname, content, content_type)
    except PhotoStorageError as error:
        return JsonResponse({"error": str(error)}, status=503)
    photo = DailyPhoto.objects.create(
        record_date=record_date,
        category=category,
        blob_url=blob_url,
        blob_pathname=blob_pathname,
        original_name=safe_original_name,
        content_type=content_type,
        size_bytes=len(content),
        position=available_positions[0],
    )
    return JsonResponse({
        "id": str(photo.id),
        "name": photo.original_name,
        "category": photo.category,
        "category_label": photo.get_category_display(),
        "content_url": reverse("photo_content", args=[photo.id]),
        "delete_url": reverse("photo_delete", args=[photo.id]),
        "count": DailyPhoto.objects.filter(record_date=record_date).count(),
    }, status=201)


def photo_delete_destination(request, photo):
    if request.POST.get("return_to") == "album":
        params = {}
        category = request.POST.get("category", "")
        if category in {value for value, _label in DailyPhoto.Category.choices}:
            params["category"] = category
        page = request.POST.get("page", "")
        if page.isascii() and page.isdecimal() and len(page) <= 6 and int(page) > 0:
            params["page"] = page
        return reverse("photo_album") + ("?" + urlencode(params) if params else "")
    return reverse("day", args=[photo.record_date])


@never_cache
@login_required
@require_POST
def photo_delete(request, photo_id):
    with transaction.atomic():
        photo = get_object_or_404(DailyPhoto.objects.select_for_update(), pk=photo_id)
        destination = photo_delete_destination(request, photo)
        if request.POST.get("confirm_delete") != "yes":
            return render(request, "photo_delete_confirm.html", {"photo": photo, "return_url": destination})
        try:
            # Remove the private file before dropping its database reference.
            # Storage failures keep the photo visible so the owner can retry.
            delete_private_photo(photo.blob_url)
        except PhotoStorageError as error:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"error": str(error)}, status=503)
            return render(request, "photo_delete_confirm.html", {
                "photo": photo, "return_url": destination, "error": str(error),
            }, status=503)
        record_date = photo.record_date
        photo.delete()
        count = DailyPhoto.objects.filter(record_date=record_date).count()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "count": count})
    messages.success(request, "사진을 삭제했습니다.")
    return redirect(destination)


@never_cache
@login_required
def photo_content(request, photo_id):
    photo = get_object_or_404(DailyPhoto, pk=photo_id)
    parsed_blob_url = urlparse(photo.blob_url)
    if parsed_blob_url.scheme != "https" or not (parsed_blob_url.hostname or "").endswith(".private.blob.vercel-storage.com"):
        raise Http404
    try:
        content = download_private_photo(photo.blob_url)
    except PhotoStorageError:
        return HttpResponse("사진을 불러오지 못했습니다.", status=503, content_type="text/plain; charset=utf-8")
    response = HttpResponse(content, content_type=photo.content_type)
    response["Content-Length"] = str(len(content))
    return response


@never_cache
@login_required
def photo_album(request):
    photos = DailyPhoto.objects.all().order_by("-record_date", "position", "created_at")
    total_photo_count = photos.count()
    selected_category = request.GET.get("category", "")
    valid_categories = {value for value, _label in DailyPhoto.Category.choices}
    if selected_category in valid_categories:
        photos = photos.filter(category=selected_category)
    else:
        selected_category = ""
    page_obj = Paginator(photos, 60).get_page(request.GET.get("page"))
    return render(request, "photo_album.html", {
        "page_obj": page_obj,
        "photo_count": photos.count(),
        "total_photo_count": total_photo_count,
        "photo_categories": DailyPhoto.Category.choices,
        "selected_category": selected_category,
    })


@never_cache
@login_required
def photo_manifest(request):
    scope = request.GET.get("scope", "")
    if scope not in {"", "new", "full"}:
        return HttpResponseBadRequest("사진 백업 종류를 확인해 주세요.")
    captured_at = timezone.now()
    photos = DailyPhoto.objects.all().order_by("record_date", "position", "created_at")
    preference = BackupReminder.objects.filter(user=request.user).first()
    if scope:
        photos = photos.filter(created_at__lte=captured_at)
    if scope == "new" and preference and preference.photos_covered_until:
        photos = photos.filter(created_at__gt=preference.photos_covered_until)
    files = []
    for photo in photos:
        base_name = PurePath(photo.original_name.replace("\\", "/")).name or f"photo-{photo.position + 1}"
        files.append({
            "url": reverse("photo_content", args=[photo.id]),
            "name": f"{photo.record_date.isoformat()}/{photo.position + 1:02d}-{base_name}",
        })
    payload = {"files": files}
    if scope and files:
        payload["confirmation_token"] = signing.dumps({
            "user": str(request.user.pk), "scope": scope, "covered_until": captured_at.isoformat(),
            "base": (preference.last_full_photos_confirmed_at.isoformat() if scope == "full" and preference and preference.last_full_photos_confirmed_at
                     else preference.photos_covered_until.isoformat() if scope == "new" and preference and preference.photos_covered_until else None),
        }, salt="photo-backup-confirmation")
    return JsonResponse(payload)

def render_task_form(request, form, heading, task=None):
    is_fragment = request.headers.get("HX-Request") == "true" or request.GET.get("fragment") == "1" or request.POST.get("fragment") == "1"
    form_action = request.path + ("?fragment=1" if is_fragment else "")
    template = "task_form_fragment.html" if is_fragment else "task_form.html"
    expected_updated_at = task.updated_at.isoformat() if task else ""
    return render(request, template, {"form": form, "heading": heading, "task": task, "form_action": form_action, "expected_updated_at": expected_updated_at})


@never_cache
@login_required
def task_create(request):
    recent_category = Task.objects.filter(deleted_at__isnull=True, category__is_archived=False).order_by("-updated_at").values_list("category_id", flat=True).first()
    default_category = recent_category or Category.objects.filter(is_archived=False).values_list("id", flat=True).first()
    default_date = "" if request.GET.get("undated") == "1" else request.GET.get("date") or timezone.localdate()
    form = TaskForm(request.POST or None, initial={"task_date": default_date, "category": default_category})
    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        with transaction.atomic():
            task.sort_order = Task.objects.select_for_update().filter(task_date=task.task_date, category=task.category, deleted_at__isnull=True).count()
            task.save()
            TaskStatusHistory.objects.create(task=task, from_status=None, to_status=task.status)
        messages.success(request, "업무를 저장했습니다.")
        if request.POST.get("save_another") == "1":
            parameters = {"date": task.task_date.isoformat() if task.task_date else "", "fragment": "1"}
            if not task.task_date:
                parameters["undated"] = "1"
            return redirect(f"{reverse('task_create')}?{urlencode(parameters)}")
        return redirect("day", selected_date=task.task_date or timezone.localdate())
    return render_task_form(request, form, "할 일 추가")

@never_cache
@login_required
def task_retry(request, task_id):
    source = get_object_or_404(Task, id=task_id, deleted_at__isnull=True, status__in=(Task.Status.NOT_DONE, Task.Status.PARTIAL))
    form = TaskForm(request.POST if request.method == "POST" else None, initial={
        "category": source.category_id, "title": source.title,
        "details": source.details, "is_important": source.is_important,
    })
    form.fields["task_date"].required = True
    # An archived source category remains available without unarchiving it.
    form.fields["category"].queryset = Category.objects.filter(Q(is_archived=False) | Q(pk=source.category_id))
    form.task_retry = True
    form.retry_token = signing.dumps({
        "source": str(source.pk), "user": str(request.user.pk), "new_task": str(uuid.uuid4()),
    }, salt="task-retry")
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["task_date"] == source.task_date:
            form.add_error("task_date", "원래 일정과 다른 날짜를 선택해 주세요.")
        try:
            ticket = signing.loads(request.POST.get("retry_token", ""), salt="task-retry", max_age=1800)
            if ticket["source"] != str(source.pk) or ticket["user"] != str(request.user.pk):
                raise ValueError
            new_id = uuid.UUID(ticket["new_task"])
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            form.add_error(None, "등록 확인 시간이 지났습니다. 내용을 확인하고 다시 저장해 주세요.")
        if not form.errors:
            with transaction.atomic():
                get_object_or_404(Task.objects.select_for_update(), id=source.pk, deleted_at__isnull=True, status__in=(Task.Status.NOT_DONE, Task.Status.PARTIAL))
                defaults = dict(form.cleaned_data)
                defaults["status"] = Task.Status.PENDING
                defaults["sort_order"] = Task.objects.filter(
                    task_date=defaults["task_date"], category=defaults["category"], deleted_at__isnull=True,
                ).count()
                task, created = Task.objects.get_or_create(pk=new_id, defaults=defaults)
                if created:
                    TaskStatusHistory.objects.create(task=task, from_status=None, to_status=Task.Status.PENDING)
            messages.success(request, "선택한 날짜에 일정을 다시 등록했습니다.")
            return redirect("day", selected_date=task.task_date)
    return render_task_form(request, form, "할 일 다시 등록")


@never_cache
@login_required
def task_edit(request, task_id):
    task = get_object_or_404(Task, id=task_id, deleted_at__isnull=True)
    form = TaskForm(request.POST or None, instance=task)
    if request.method == "POST":
        if not form.is_valid():
            return render_task_form(request, form, "할 일 수정", task)
        with transaction.atomic():
            latest = get_object_or_404(Task.objects.select_for_update(), id=task.id, deleted_at__isnull=True)
            if request.POST.get("expected_updated_at") != latest.updated_at.isoformat():
                is_fragment = request.headers.get("HX-Request") == "true" or request.GET.get("fragment") == "1"
                template = "task_edit_conflict_fragment.html" if is_fragment else "task_edit_conflict.html"
                return render(request, template, {"task": latest, "submitted": form.cleaned_data}, status=409)
            form = TaskForm(request.POST, instance=latest)
            if not form.is_valid():
                return render_task_form(request, form, "할 일 수정", latest)
            previous = (latest.task_date, latest.category_id)
            updated = form.save(commit=False)
            if previous != (updated.task_date, updated.category_id):
                updated.sort_order = Task.objects.filter(task_date=updated.task_date, category=updated.category, deleted_at__isnull=True).exclude(pk=updated.pk).count()
            updated.save()
        messages.success(request, "업무를 수정했습니다.")
        return redirect("day", selected_date=updated.task_date or timezone.localdate())
    return render_task_form(request, form, "할 일 수정", task)

@login_required
@require_POST
def task_status(request, task_id):
    is_fragment_request = request.headers.get("HX-Request") == "true"
    new_status = request.POST.get("status")
    if new_status not in Task.Status.values:
        raise Http404
    status_changed = False
    with transaction.atomic():
        task = get_object_or_404(Task.objects.select_for_update(), id=task_id, deleted_at__isnull=True)
        if new_status != task.status:
            old_status = task.status
            task.status = new_status
            task.save(update_fields=["status", "updated_at"])
            TaskStatusHistory.objects.create(task=task, from_status=old_status, to_status=new_status)
            status_changed = True
    if is_fragment_request:
        ordered = tasks_in_status_order(Task.objects.filter(
            task_date=task.task_date, category_id=task.category_id, deleted_at__isnull=True,
        ), "sort_order", "created_at")
        return JsonResponse({"status": task.status, "label": task.get_status_display(),
                             "task_ids": [str(task_id) for task_id in ordered.values_list("pk", flat=True)],
                             **weekly_update(task.task_date, request)})
    if status_changed:
        messages.success(request, "상태를 저장했습니다.")
    return redirect("day", selected_date=task.task_date or timezone.localdate())

@login_required
@require_POST
def task_delete(request, task_id):
    with transaction.atomic():
        task = get_object_or_404(Task.objects.select_for_update(), id=task_id, deleted_at__isnull=True)
        task.deleted_at = timezone.now()
        task.save(update_fields=["deleted_at", "updated_at"])
    messages.success(request, "업무를 삭제했습니다. 30일 동안 휴지통에서 복구할 수 있습니다.")
    return redirect("day", selected_date=task.task_date or timezone.localdate())

@never_cache
@login_required
def inbox(request):
    tasks = tasks_in_status_order(Task.objects.filter(task_date__isnull=True, deleted_at__isnull=True).select_related("category"), "category__sort_order", "category__name", "sort_order", "created_at")
    return render(request, "inbox.html", {"tasks": tasks})

@never_cache
@login_required
def categories(request):
    editing = get_object_or_404(Category, pk=request.GET["edit"]) if request.GET.get("edit") else None
    form = CategoryForm(request.POST or None, instance=editing)
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        if not category.pk:
            category.sort_order = Category.objects.filter(is_archived=False).count()
        category.save()
        messages.success(request, "카테고리를 저장했습니다.")
        return redirect("categories")
    return render(request, "categories.html", {"form": form, "editing": editing, "categories": Category.objects.all()})

@login_required
@require_POST
def category_archive(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    category.is_archived = not category.is_archived
    category.save(update_fields=["is_archived", "updated_at"])
    return redirect("categories")

@login_required
@require_POST
def task_move(request, task_id):
    with transaction.atomic():
        task = get_object_or_404(Task.objects.select_for_update(), pk=task_id, deleted_at__isnull=True)
        ordered = list(Task.objects.select_for_update().filter(task_date=task.task_date, category=task.category, status=task.status, deleted_at__isnull=True).order_by("sort_order", "created_at", "pk"))
        current = ordered.index(task)
        target = current + (-1 if request.POST.get("direction") == "up" else 1)
        if 0 <= target < len(ordered):
            ordered[current], ordered[target] = ordered[target], ordered[current]
            changed_at = timezone.now()
            for position, item in enumerate(ordered):
                if item.sort_order != position:
                    Task.objects.filter(pk=item.pk).update(sort_order=position, updated_at=changed_at)
    return redirect("day", selected_date=task.task_date or timezone.localdate())


@never_cache
@login_required
@require_POST
def task_reorder(request):
    category = get_object_or_404(Category, pk=request.POST.get("category_id"))
    raw_date = request.POST.get("task_date", "")
    try:
        task_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        return HttpResponseBadRequest("업무 날짜가 올바르지 않습니다.")
    task_ids = request.POST.getlist("task_ids")
    with transaction.atomic():
        group = list(Task.objects.select_for_update().filter(task_date=task_date, category=category, deleted_at__isnull=True).order_by("sort_order", "created_at"))
        expected_ids = {str(task.pk) for task in group}
        if len(task_ids) != len(expected_ids) or set(task_ids) != expected_ids:
            return HttpResponseBadRequest("같은 날짜와 카테고리의 업무만 다시 정렬할 수 있습니다.")
        by_id = {str(task.pk): task for task in group}
        ranks = {status: rank for rank, status in enumerate(TASK_STATUS_ORDER)}
        # Status groups are fixed; submitted order is preserved within each group.
        task_ids = sorted(task_ids, key=lambda task_id: ranks.get(by_id[task_id].status, len(ranks)))
        changed_at = timezone.now()
        for position, task_id in enumerate(task_ids):
            task = by_id[task_id]
            if task.sort_order != position:
                Task.objects.filter(pk=task.pk).update(sort_order=position, updated_at=changed_at)
    if request.headers.get("HX-Request") == "true":
        return JsonResponse({"ok": True, "task_ids": task_ids, **weekly_update(task_date, request)})
    return redirect("day", selected_date=task_date or timezone.localdate())

@login_required
@require_POST
def category_move(request, category_id):
    with transaction.atomic():
        category = get_object_or_404(Category.objects.select_for_update(), pk=category_id)
        ordered = list(Category.objects.select_for_update().filter(is_archived=category.is_archived).order_by("sort_order", "name"))
        current = ordered.index(category)
        target = current + (-1 if request.POST.get("direction") == "up" else 1)
        if 0 <= target < len(ordered):
            ordered[current], ordered[target] = ordered[target], ordered[current]
            changed_at = timezone.now()
            for position, item in enumerate(ordered):
                if item.sort_order != position:
                    Category.objects.filter(pk=item.pk).update(sort_order=position, updated_at=changed_at)
    return redirect("categories")


@never_cache
@login_required
def settings_home(request):
    return render(request, "settings.html")


@never_cache
@login_required
def task_trash(request):
    cutoff = timezone.now() - timedelta(days=30)
    tasks = Task.objects.filter(deleted_at__gte=cutoff).select_related("category").order_by("-deleted_at")
    return render(request, "task_trash.html", {"tasks": tasks, "cutoff": cutoff})


@never_cache
@login_required
@require_POST
def task_restore(request, task_id):
    cutoff = timezone.now() - timedelta(days=30)
    with transaction.atomic():
        task = get_object_or_404(Task.objects.select_for_update(), pk=task_id, deleted_at__gte=cutoff)
        task.sort_order = Task.objects.select_for_update().filter(task_date=task.task_date, category=task.category, deleted_at__isnull=True).count()
        task.deleted_at = None
        task.save(update_fields=["deleted_at", "sort_order", "updated_at"])
    messages.success(request, "업무를 복구했습니다.")
    if task.task_date:
        return redirect("day", selected_date=task.task_date)
    return redirect("inbox")


def filtered_tasks(form):
    if form.is_bound and form.is_valid():
        filters = form.cleaned_data
    else:
        filters = {"date_mode": "dated", "ordering": "status"}
    tasks = Task.objects.filter(deleted_at__isnull=True).select_related("category")
    mode = filters.get("date_mode") or "dated"
    if mode == "inbox":
        tasks = tasks.filter(task_date__isnull=True)
    elif mode != "all":
        tasks = tasks.filter(task_date__isnull=False)
    if filters.get("date_from"):
        tasks = tasks.filter(task_date__gte=filters["date_from"])
    if filters.get("date_to"):
        tasks = tasks.filter(task_date__lte=filters["date_to"])
    categories = filters.get("categories")
    if categories:
        tasks = tasks.filter(category__in=categories)
    statuses = filters.get("statuses")
    if statuses:
        tasks = tasks.filter(status__in=statuses)
    if filters.get("important"):
        tasks = tasks.filter(is_important=True)
    query = (filters.get("q") or "").strip()
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(details__icontains=query))
    ordering = filters.get("ordering") or "status"
    if ordering == "status":
        return tasks_in_status_order(tasks, F("task_date").desc(nulls_last=True),
                                     "category__sort_order", "category__name", "sort_order", "created_at")
    if ordering == "updated_desc":
        return tasks.order_by("-updated_at", "category__sort_order", "sort_order")
    if ordering == "date_asc":
        return tasks.order_by(F("task_date").asc(nulls_last=True), "category__sort_order", "sort_order")
    return tasks.order_by(F("task_date").desc(nulls_last=True), "category__sort_order", "sort_order")


@never_cache
@login_required
def task_list(request):
    form = TaskFilterForm(request.GET or None)
    tasks = filtered_tasks(form)
    page_obj = Paginator(tasks, 50).get_page(request.GET.get("page"))
    return render(request, "task_list.html", {"form": form, "page_obj": page_obj, "query_string": request.GET.copy()})


def filtered_records(form):
    records = DailyRecord.objects.exclude(today_story="", memorable_sentence="", stayed_with_me="")
    filters = form.cleaned_data if form.is_bound and form.is_valid() else {}
    if filters.get("date_from"):
        records = records.filter(record_date__gte=filters["date_from"])
    if filters.get("date_to"):
        records = records.filter(record_date__lte=filters["date_to"])
    section = filters.get("section") or ""
    if section:
        records = records.exclude(**{section: ""})
    query = (filters.get("q") or "").strip()
    if query:
        if section:
            records = records.filter(**{f"{section}__icontains": query})
        else:
            records = records.filter(Q(today_story__icontains=query) | Q(memorable_sentence__icontains=query) | Q(stayed_with_me__icontains=query))
    return records.order_by("-record_date")


@never_cache
@login_required
def record_list(request):
    form = RecordFilterForm(request.GET or None)
    page_obj = Paginator(filtered_records(form), 50).get_page(request.GET.get("page"))
    selected_section = form.cleaned_data.get("section", "") if form.is_bound and form.is_valid() else ""
    return render(request, "record_list.html", {"form": form, "page_obj": page_obj, "query_string": request.GET.copy(), "selected_section": selected_section})


def attachment(data, content_type, filename):
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return response


@never_cache
@login_required
def export_tasks(request, extension):
    if extension not in ("xlsx", "csv"):
        raise Http404
    form = TaskFilterForm(request.GET or None)
    if form.is_bound and not form.is_valid():
        return HttpResponseBadRequest("내보내기 필터를 확인해 주세요.")
    tasks = filtered_tasks(form)
    filename = export_filename("tasks", extension)
    if extension == "xlsx":
        return attachment(workbook_for_tasks(tasks), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename)
    return attachment(csv_for_tasks(tasks), "text/csv; charset=utf-8", filename)


@never_cache
@login_required
def export_records(request, extension):
    if extension not in ("xlsx", "csv"):
        raise Http404
    form = RecordFilterForm(request.GET or None)
    if form.is_bound and not form.is_valid():
        return HttpResponseBadRequest("내보내기 필터를 확인해 주세요.")
    records = filtered_records(form)
    filename = export_filename("records", extension)
    if extension == "xlsx":
        return attachment(workbook_for_records(records), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename)
    return attachment(csv_for_records(records), "text/csv; charset=utf-8", filename)


@never_cache
@login_required
def backup_page(request):
    from django.conf import settings
    request.session["backup_reminder_quiet_until"] = (timezone.now() + timedelta(hours=1)).timestamp()
    return render(request, "backup.html", {
        "last_backup": BackupLog.objects.first(),
        "postgres_backup": connections["default"].vendor == "postgresql",
        "provider_backups_configured": settings.DATABASE_PROVIDER_BACKUPS_CONFIGURED,
        "has_photos": DailyPhoto.objects.exists(),
    })


@never_cache
@login_required
@require_POST
def backup_reminder_update(request):
    action = request.POST.get("action")
    if action not in {"snooze", "interval", "confirm"}:
        return HttpResponseBadRequest("백업 알림 설정을 확인해 주세요.")
    if action == "interval":
        try:
            interval = int(request.POST.get("interval_days", ""))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("백업 주기를 확인해 주세요.")
        if interval not in {7, 14, 30}:
            return HttpResponseBadRequest("백업 주기를 확인해 주세요.")
    if action == "confirm":
        return confirm_backup_storage(request)
    preference, _ = BackupReminder.objects.get_or_create(user=request.user)
    if action == "snooze":
        preference.snoozed_until = timezone.now() + timedelta(days=1)
        preference.save(update_fields=["snoozed_until"])
    elif action == "interval":
        preference.interval_days = interval
        preference.save(update_fields=["interval_days"])
        messages.success(request, f"백업 알림을 {interval}일 주기로 설정했습니다.")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return redirect("backup_page")


def confirm_backup_storage(request):
    kind = request.POST.get("kind", "data")
    if kind not in {"data", "new", "full"}:
        return HttpResponseBadRequest("백업 종류를 확인해 주세요.")
    now = timezone.now()
    photo_receipt = None
    if kind != "data":
        try:
            photo_receipt = signing.loads(request.POST.get("confirmation_token", ""), salt="photo-backup-confirmation", max_age=7 * 86400)
            covered_until = parse_datetime(photo_receipt["covered_until"])
            if (photo_receipt["user"] != str(request.user.pk) or photo_receipt["scope"] != kind
                    or not covered_until or timezone.is_naive(covered_until) or covered_until > now):
                raise ValueError
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            messages.error(request, "사진 ZIP을 다시 내려받아 보관한 뒤 저장 확인을 눌러 주세요.")
            return redirect("backup_page")
    with transaction.atomic():
        preference, _ = BackupReminder.objects.get_or_create(user=request.user)
        preference = BackupReminder.objects.select_for_update().get(pk=preference.pk)
        if kind == "data":
            download_id = request.session.get("backup_data_download")
            latest = BackupLog.objects.filter(pk=download_id, backup_type=BackupLog.BackupType.MANUAL, status=BackupLog.Status.SUCCESS).first() if download_id else None
            if (not latest or request.POST.get("saved_schedule") != "yes"
                    or not latest.completed_at or latest.completed_at < now - timedelta(days=7)
                    or (preference.last_confirmed_at and latest.started_at <= preference.last_confirmed_at)):
                messages.error(request, "이 브라우저에서 일정·기록 ZIP을 새로 내려받아 보관한 뒤 저장 확인을 눌러 주세요.")
                return redirect("backup_page")
            preference.last_confirmed_at = now
        else:
            baseline = preference.last_full_photos_confirmed_at if kind == "full" else preference.photos_covered_until
            if (request.POST.get("saved_photos") != "yes" or photo_receipt.get("base") != (baseline.isoformat() if baseline else None)
                    or (preference.photos_covered_until and covered_until < preference.photos_covered_until)):
                messages.error(request, "사진 ZIP을 새로 내려받아 실제 보관 여부를 확인해 주세요.")
                return redirect("backup_page")
            preference.photos_covered_until = covered_until
            preference.last_new_photos_confirmed_at = now
            if kind == "full":
                preference.last_full_photos_confirmed_at = now
        seen = dict(preference.popup_seen)
        for confirmed_kind in ({"full", "new"} if kind == "full" else {kind}):
            seen.pop(confirmed_kind, None)
        preference.popup_seen = seen
        preference.snoozed_until = None
        preference.save()
    if kind == "data":
        request.session.pop("backup_data_download", None)
    labels = {"data": "일정·기록", "new": "새 사진", "full": "전체 사진"}
    messages.success(request, f"{labels[kind]} 파일의 저장 확인을 기록했습니다.")
    return redirect("backup_page")


@never_cache
@login_required
@require_POST
def create_backup(request):
    password = request.POST.get("password", "")
    if authenticate(request, username=request.user.get_username(), password=password) is None:
        return render(request, "backup.html", {"error": "비밀번호를 확인해 주세요.", "last_backup": BackupLog.objects.first()}, status=403)
    started_at = timezone.now()
    log = BackupLog.objects.create(backup_type=BackupLog.BackupType.MANUAL, file_name="", sha256="", status=BackupLog.Status.STARTED, started_at=started_at)
    try:
        archive, timestamp = create_full_backup()
    except Exception as error:
        log.status = BackupLog.Status.FAILED
        log.completed_at = timezone.now()
        log.message = f"수동 백업 실패: {type(error).__name__}"
        log.save(update_fields=["status", "completed_at", "message"])
        return render(request, "backup.html", {"error": "백업 파일을 만들지 못했습니다. 데이터는 변경되지 않았습니다.", "last_backup": BackupLog.objects.first()}, status=503)
    filename = f"schedule-backup-{timestamp}.zip"
    log.file_name = filename
    log.sha256 = hashlib.sha256(archive).hexdigest()
    log.status = BackupLog.Status.SUCCESS
    log.completed_at = timezone.now()
    log.message = "수동 전체 백업을 생성했습니다."
    log.save(update_fields=["file_name", "sha256", "status", "completed_at", "message"])
    request.session["backup_data_download"] = str(log.pk)
    response = HttpResponse(archive, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required
@require_POST
def restore_backup(request):
    if connections["default"].vendor == "postgresql":
        return HttpResponseBadRequest("운영 DB 복원은 쓰기를 중단한 후 restore_data_backup 관리 명령으로 진행하세요.")
    password = request.POST.get("password", "")
    if authenticate(request, username=request.user.get_username(), password=password) is None:
        return render(request, "backup.html", {"restore_error": "비밀번호를 확인해 주세요.", "last_backup": BackupLog.objects.first()}, status=403)
    if request.POST.get("confirm_restore") != "yes":
        return render(request, "backup.html", {"restore_error": "복원 확인 항목을 선택해 주세요.", "last_backup": BackupLog.objects.first()}, status=400)
    uploaded = request.FILES.get("backup_file")
    if uploaded is None or uploaded.size == 0 or uploaded.size > MAX_ARCHIVE_SIZE:
        return render(request, "backup.html", {"restore_error": "ZIP 파일이 없거나 허용 크기를 초과했습니다.", "last_backup": BackupLog.objects.first()}, status=400)
    try:
        counts = restore_database_archive(uploaded.read())
    except BackupValidationError as error:
        return render(request, "backup.html", {"restore_error": str(error), "last_backup": BackupLog.objects.first()}, status=400)
    except (BackupRestoreError, DatabaseError, sqlite3.Error, OSError, ImproperlyConfigured):
        return render(request, "backup.html", {"restore_error": "복원을 완료하지 못했습니다. 현재 데이터는 변경되지 않았습니다.", "last_backup": BackupLog.objects.first()}, status=503)
    logout(request)
    return render(request, "restore_success.html", {"counts": counts})
