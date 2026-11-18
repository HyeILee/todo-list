from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

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
    return render(request, "memo_board.html", {"notes": StickyNote.objects.all(), "form": form})


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
