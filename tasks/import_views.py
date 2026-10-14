import secrets
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .models import Category
from .task_imports import MAX_FILE_BYTES, TaskImportError, mark_duplicates, parse_workbook, register_rows, template_workbook


DRAFT_KEY = "excel_task_import"
DRAFT_MAX_AGE = 30 * 60


class TaskImportForm(forms.Form):
    workbook = forms.FileField(label="일정 엑셀 파일", widget=forms.ClearableFileInput(attrs={"accept": ".xlsx", "aria-describedby": "import-file-help import-file-errors"}))

    def clean_workbook(self):
        uploaded = self.cleaned_data["workbook"]
        if not uploaded.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("xlsx 파일을 선택해 주세요. xls·CSV·사진 ZIP은 지원하지 않습니다.")
        if not uploaded.size or uploaded.size > MAX_FILE_BYTES:
            raise forms.ValidationError("xlsx 파일은 2MB 이하여야 합니다.")
        return uploaded


@never_cache
@login_required
@require_GET
def task_import_template(request):
    response = HttpResponse(template_workbook(Category.objects.filter(is_archived=False)), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="schedule-import-template.xlsx"'
    return response


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def task_import(request):
    form = TaskImportForm()
    rows = []
    filename = ""
    token = ""
    page_error = ""
    status = 200
    if request.method == "GET":
        request.session.pop(DRAFT_KEY, None)
    if request.method == "POST" and request.POST.get("action") == "register":
        draft = request.session.get(DRAFT_KEY)
        if not draft or not secrets.compare_digest(str(draft.get("token", "")), request.POST.get("token", "")) or timezone.now().timestamp() - draft["created_at"] > DRAFT_MAX_AGE:
            page_error = "미리보기가 만료되었거나 이미 처리되었습니다. 파일을 다시 올려 주세요."
            status = 400
        else:
            try:
                count, skipped, replayed = register_rows(draft["rows"], draft["token"], request.POST.get("include_duplicates") == "yes")
            except TaskImportError as error:
                page_error = str(error)
                status = 400
            except DatabaseError:
                page_error = "일정을 등록하지 못했습니다. 잠시 후 파일을 다시 올려 주세요. 기존 일정은 유지됩니다."
                status = 503
            else:
                request.session.pop(DRAFT_KEY, None)
                if replayed:
                    messages.info(request, "이미 처리한 파일입니다. 일정을 다시 추가하지 않았습니다.")
                else:
                    messages.success(request, f"일정 {count}건을 등록했습니다. 중복 {skipped}건은 건너뛰었습니다.")
                return redirect("task_list")
    elif request.method == "POST":
        request.session.pop(DRAFT_KEY, None)
        form = TaskImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["workbook"]
            filename = uploaded.name
            try:
                rows = mark_duplicates(parse_workbook(uploaded.read(MAX_FILE_BYTES + 1), Category.objects.filter(is_archived=False)))
            except TaskImportError as error:
                form.add_error("workbook", str(error))
                status = 400
            else:
                if any(row["errors"] for row in rows):
                    page_error = "입력 오류가 있어 등록할 수 없습니다. 표시한 행을 고친 뒤 파일을 다시 올려 주세요."
                    status = 400
                else:
                    token = str(uuid.uuid4())
                    request.session[DRAFT_KEY] = {"token": token, "created_at": timezone.now().timestamp(), "rows": rows}
        else:
            status = 400
    return render(request, "task_import.html", {
        "form": form, "rows": rows, "filename": filename, "token": token, "page_error": page_error,
        "total_count": len(rows), "error_count": sum(bool(row["errors"]) for row in rows),
        "duplicate_count": sum(bool(row.get("duplicate")) and not row["errors"] for row in rows),
        "ready_count": sum(not row["errors"] and not row.get("duplicate") for row in rows),
        "categories": Category.objects.filter(is_archived=False),
    }, status=status)
