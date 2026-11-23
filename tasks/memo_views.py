from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .models import StickyNote


class MemoForm(forms.ModelForm):
    version = forms.DateTimeField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = StickyNote
        fields = ["content", "color"]
        labels = {"content": "기억할 내용", "color": "포스트잇 색"}
        widgets = {"content": forms.Textarea(attrs={"rows": 6, "maxlength": 3000, "placeholder": "잊지 않고 기억할 내용을 적어 주세요."})}


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def memo_board(request):
    form = MemoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "메모를 붙였어요.")
        return redirect("memo_board")
    archived = request.GET.get("view") == "archive"
    return render(request, "memo_board.html", {
        "notes": StickyNote.objects.filter(is_archived=archived), "form": form, "archived": archived,
        "board_count": StickyNote.objects.filter(is_archived=False).count(),
        "archive_count": StickyNote.objects.filter(is_archived=True).count(),
    })


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def memo_edit(request, note_id):
    note = get_object_or_404(StickyNote, pk=note_id)
    form = MemoForm(request.POST or None, instance=note, initial={"version": note.updated_at})
    status = 200
    if request.method == "POST" and form.is_valid():
        changed = StickyNote.objects.filter(pk=note.pk, updated_at=form.cleaned_data["version"]).update(
            content=form.cleaned_data["content"], color=form.cleaned_data["color"], updated_at=timezone.now())
        if changed:
            messages.success(request, "메모를 수정했어요.")
            return redirect("memo_board")
        form.add_error(None, "다른 화면에서 이 메모가 변경됐어요. 작성한 내용을 복사한 뒤 메모장을 다시 열어 확인해 주세요.")
        status = 409
    return render(request, "memo_edit.html", {"form": form, "note": note}, status=status)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def memo_delete(request, note_id):
    note = get_object_or_404(StickyNote, pk=note_id)
    error = ""
    if request.method == "POST":
        try:
            version = parse_datetime(request.POST.get("version", ""))
        except ValueError:
            version = None
        if version == note.updated_at:
            deleted, _ = StickyNote.objects.filter(pk=note.pk, updated_at=version).delete()
            if deleted:
                messages.success(request, "메모를 삭제했어요.")
                return redirect("memo_board")
        error = "메모가 변경됐어요. 내용을 다시 확인한 뒤 삭제해 주세요."
    return render(request, "memo_delete.html", {"note": note, "error": error}, status=409 if error else 200)


@login_required
@never_cache
@require_POST
def memo_move(request, note_id):
    note = get_object_or_404(StickyNote, pk=note_id)
    action = request.POST.get("action")
    if action not in {"archive", "pin"}:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("이동할 위치를 확인해 주세요.")
    try:
        version = parse_datetime(request.POST.get("version", ""))
    except ValueError:
        version = None
    updated = StickyNote.objects.filter(pk=note.pk, updated_at=version).update(
        is_archived=action == "archive", updated_at=timezone.now())
    if updated:
        messages.success(request, "쪽지를 보관했어요." if action == "archive" else "쪽지를 보드에 붙였어요.")
    else:
        messages.warning(request, "다른 화면에서 쪽지가 변경됐어요. 내용을 확인한 뒤 다시 이동해 주세요.")
    response = redirect("memo_board")
    if action == "pin":
        response["Location"] += "?view=archive"
    return response
